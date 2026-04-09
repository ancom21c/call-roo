#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

SERVICE_NAME="callroo-printer"
SERVICE_USER="${SUDO_USER:-${USER}}"
SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
INSTALL_DIR="/opt/callroo-printer"
PYTHON_BIN="$(command -v python3)"
VENV_DIR=""
CONFIG_PATH=""
ENV_FILE="/etc/default/callroo-printer"
ENV_EXAMPLE="${REPO_ROOT}/deploy/systemd/callroo-printer.env.example"
PREPARE_TIMINIPRINT_SCRIPT="${REPO_ROOT}/scripts/apply_timiniprint_patches.sh"
LOG_LEVEL="INFO"
DRY_RUN=0

usage() {
  cat <<EOF
Usage: sudo bash deploy/systemd/install.sh [options]

Options:
  --service-name NAME   systemd service name. Default: ${SERVICE_NAME}
  --user USER           service user. Default: ${SERVICE_USER}
  --group GROUP         service group. Default: ${SERVICE_GROUP}
  --install-dir PATH    install directory. Default: ${INSTALL_DIR}
  --python PATH         bootstrap python executable. Default: ${PYTHON_BIN}
  --venv-dir PATH       virtualenv directory. Default: <install-dir>/.venv
  --config PATH         config.json path inside the installed copy.
                        Default: <install-dir>/config.json
  --env-file PATH       EnvironmentFile path. Default: ${ENV_FILE}
  --log-level LEVEL     INFO, DEBUG, WARNING, ERROR. Default: ${LOG_LEVEL}
  --dry-run             start service with --dry-run
  --help                show this help
EOF
}


sync_install_tree() {
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude ".git" \
      --exclude ".git/" \
      --exclude "/.venv/" \
      --exclude "vendor/TiMini-Print/.venv/" \
      --exclude "__pycache__/" \
      --exclude "*.pyc" \
      --exclude ".pytest_cache/" \
      --exclude "logs/" \
      --exclude "outputs/" \
      --exclude "tests/" \
      --exclude "deploy/systemd/callroo-printer.env" \
      "${REPO_ROOT}/" "${INSTALL_DIR}/"
    return
  fi

  echo "rsync not found; using tar fallback without delete sync." >&2
  tar \
    --exclude=".git" \
    --exclude="./.venv" \
    --exclude="./.venv/*" \
    --exclude="./vendor/TiMini-Print/.venv" \
    --exclude="./vendor/TiMini-Print/.venv/*" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude=".pytest_cache" \
    --exclude="logs" \
    --exclude="outputs" \
    --exclude="tests" \
    --exclude="deploy/systemd/callroo-printer.env" \
    -C "${REPO_ROOT}" -cf - . | tar -C "${INSTALL_DIR}" -xf -
}

prepare_timiniprint_submodule() {
  if [[ ! -f "${PREPARE_TIMINIPRINT_SCRIPT}" ]]; then
    return
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "git not found. It is required to initialize and patch vendor/TiMini-Print." >&2
    exit 1
  fi

  bash "${PREPARE_TIMINIPRINT_SCRIPT}"
}

rewrite_installed_config() {
  python3 - <<PY
import json
from pathlib import Path

config_path = Path(${CONFIG_PATH@Q})
payload = json.loads(config_path.read_text(encoding="utf-8"))
bluetooth = payload.setdefault("bluetooth", {})
backend = str(bluetooth.get("backend", "")).strip().lower()
if backend in {"timiniprint_cli_direct", "timiniprint-mxw01-direct", "timiniprint"}:
    bluetooth["timiniprint_repo"] = "vendor/TiMini-Print"
    bluetooth["timiniprint_cli"] = None
    bluetooth["timiniprint_python"] = None
config_path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
}

ensure_runtime_venv() {
  local venv_python
  venv_python="${VENV_DIR}/bin/python"

  if [[ ! -x "${venv_python}" ]]; then
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  fi

  "${venv_python}" -m pip install --upgrade pip
  "${venv_python}" -m pip install -r "${INSTALL_DIR}/requirements.txt"
}


installed_backend_requires_timiniprint() {
  python3 - <<PY
import json
from pathlib import Path

config_path = Path(${CONFIG_PATH@Q})
payload = json.loads(config_path.read_text(encoding="utf-8"))
backend = str(payload.get("bluetooth", {}).get("backend", "")).strip().lower()
requires = backend in {"timiniprint_cli_direct", "timiniprint-mxw01-direct", "timiniprint"}
raise SystemExit(0 if requires else 1)
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service-name)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --user)
      SERVICE_USER="$2"
      SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
      shift 2
      ;;
    --group)
      SERVICE_GROUP="$2"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --venv-dir)
      VENV_DIR="$2"
      shift 2
      ;;
    --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --log-level)
      LOG_LEVEL="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo or as root." >&2
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found or not executable: ${PYTHON_BIN}" >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found." >&2
  exit 1
fi

mkdir -p "${INSTALL_DIR}"
prepare_timiniprint_submodule
sync_install_tree

if [[ -z "${VENV_DIR}" ]]; then
  VENV_DIR="${INSTALL_DIR}/.venv"
fi

if [[ -z "${CONFIG_PATH}" ]]; then
  CONFIG_PATH="${INSTALL_DIR}/config.json"
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config file not found in installed copy: ${CONFIG_PATH}" >&2
  echo "Copy config.example.json to config.json and fill in local values before installing." >&2
  exit 1
fi

if installed_backend_requires_timiniprint; then
  rewrite_installed_config
fi

ensure_runtime_venv

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}"

if [[ ! -f "${ENV_FILE}" && -f "${ENV_EXAMPLE}" ]]; then
  install -m 600 "${ENV_EXAMPLE}" "${ENV_FILE}"
  echo "Created ${ENV_FILE} from example. Fill in real values if needed."
fi

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
DRY_RUN_ARG=""
if [[ "${DRY_RUN}" -eq 1 ]]; then
  DRY_RUN_ARG=" --dry-run"
fi

cat >"${UNIT_PATH}" <<EOF
[Unit]
Description=Call Roo Printer Service
After=network-online.target bluetooth.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -m callroo_printer --config ${CONFIG_PATH} --log-level ${LOG_LEVEL}${DRY_RUN_ARG}
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=25

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "${UNIT_PATH}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null
if systemctl is-active --quiet "${SERVICE_NAME}"; then
  systemctl restart "${SERVICE_NAME}"
else
  systemctl start "${SERVICE_NAME}"
fi

echo "Installed ${UNIT_PATH}"
echo "Service status:"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
echo
echo "If you change the implementation, assets, config.json, or the service options, rerun this script or run:"
echo "  sudo systemctl restart ${SERVICE_NAME}"
