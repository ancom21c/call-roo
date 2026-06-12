#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
INSTALL_SCRIPT="${REPO_ROOT}/deploy/systemd/install.sh"
LOCAL_CONFIG_PATH="${REPO_ROOT}/config.json"

SERVICE_NAME="callroo-printer"
DASHBOARD_SERVICE_NAME="callroo-dashboard"
INSTALL_DIR=""
SERVICE_USER=""
SERVICE_GROUP=""
CONFIG_PATH=""
ENV_FILE=""
LOG_LEVEL=""
FORCE_DRY_RUN=""
SKIP_STATUS=0
SKIP_DASHBOARD_RESTART=0

usage() {
  cat <<EOF
Usage: bash scripts/deploy.sh [options]

Deploy the current worktree and config.json to the installed callroo-printer service.

Options:
  --service-name NAME   systemd service name to inspect and deploy.
                        Default: ${SERVICE_NAME}
  --dashboard-service-name NAME
                        dashboard systemd service to restart when installed.
                        Default: ${DASHBOARD_SERVICE_NAME}
  --install-dir PATH    override install directory.
  --user USER           override service user.
  --group GROUP         override service group.
  --config PATH         override runtime config path.
  --env-file PATH       override EnvironmentFile path.
  --log-level LEVEL     override service log level.
  --dry-run             deploy service in --dry-run mode.
  --no-dry-run          deploy service without --dry-run mode.
  --skip-dashboard-restart
                        do not restart the dashboard service after deploy.
  --skip-status         do not print final systemctl status/journal hints.
  --help                show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service-name)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --dashboard-service-name)
      DASHBOARD_SERVICE_NAME="$2"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --user)
      SERVICE_USER="$2"
      shift 2
      ;;
    --group)
      SERVICE_GROUP="$2"
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
      FORCE_DRY_RUN="1"
      shift
      ;;
    --no-dry-run)
      FORCE_DRY_RUN="0"
      shift
      ;;
    --skip-status)
      SKIP_STATUS=1
      shift
      ;;
    --skip-dashboard-restart)
      SKIP_DASHBOARD_RESTART=1
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

if [[ ! -f "${INSTALL_SCRIPT}" ]]; then
  echo "Install script not found: ${INSTALL_SCRIPT}" >&2
  exit 1
fi

if [[ ! -f "${LOCAL_CONFIG_PATH}" ]]; then
  echo "Local config.json not found: ${LOCAL_CONFIG_PATH}" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found." >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found." >&2
  exit 1
fi

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo not found." >&2
  exit 1
fi

mapfile -t detected_values < <(
  python3 - "${SERVICE_NAME}" <<'PY'
import shlex
import subprocess
import sys
from pathlib import Path

service_name = sys.argv[1]
result = subprocess.run(
    [
        "systemctl",
        "show",
        service_name,
        "--property=LoadState,User,Group,EnvironmentFile,WorkingDirectory,ExecStart",
        "--no-page",
    ],
    capture_output=True,
    text=True,
    check=False,
)
payload: dict[str, str] = {}
for raw_line in result.stdout.splitlines():
    if "=" not in raw_line:
        continue
    key, value = raw_line.split("=", 1)
    payload[key.strip()] = value.strip()

user = payload.get("User", "")
group = payload.get("Group", "")
env_file = payload.get("EnvironmentFile", "").lstrip("-")
working_directory = payload.get("WorkingDirectory", "")
exec_start = payload.get("ExecStart", "")
load_state = payload.get("LoadState", "")

config_path = ""
log_level = ""
dry_run = "0"

argv_marker = "argv[]="
argv_index = exec_start.find(argv_marker)
if argv_index != -1:
    argv_blob = exec_start[argv_index + len(argv_marker):]
    argv_blob = argv_blob.split(" ;", 1)[0].strip()
    try:
        argv = shlex.split(argv_blob)
    except ValueError:
        argv = []
    for index, token in enumerate(argv):
        if token == "--config" and index + 1 < len(argv):
            config_path = argv[index + 1]
        elif token.startswith("--config="):
            config_path = token.split("=", 1)[1]
        elif token == "--log-level" and index + 1 < len(argv):
            log_level = argv[index + 1]
        elif token.startswith("--log-level="):
            log_level = token.split("=", 1)[1]
        elif token == "--dry-run":
            dry_run = "1"

if not config_path and working_directory:
    config_path = str(Path(working_directory) / "config.json")

values = (
    load_state,
    user,
    group,
    env_file,
    working_directory,
    config_path,
    log_level,
    dry_run,
)
for value in values:
    print(value)
PY
)

DETECTED_LOAD_STATE="${detected_values[0]:-}"
DETECTED_USER="${detected_values[1]:-}"
DETECTED_GROUP="${detected_values[2]:-}"
DETECTED_ENV_FILE="${detected_values[3]:-}"
DETECTED_INSTALL_DIR="${detected_values[4]:-}"
DETECTED_CONFIG_PATH="${detected_values[5]:-}"
DETECTED_LOG_LEVEL="${detected_values[6]:-}"
DETECTED_DRY_RUN="${detected_values[7]:-0}"

if [[ -z "${SERVICE_USER}" ]]; then
  SERVICE_USER="${DETECTED_USER:-${USER}}"
fi
if [[ -z "${SERVICE_GROUP}" ]]; then
  if [[ -n "${DETECTED_GROUP}" ]]; then
    SERVICE_GROUP="${DETECTED_GROUP}"
  else
    SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
  fi
fi
if [[ -z "${INSTALL_DIR}" ]]; then
  INSTALL_DIR="${DETECTED_INSTALL_DIR:-/opt/callroo-printer}"
fi
if [[ -z "${CONFIG_PATH}" ]]; then
  CONFIG_PATH="${DETECTED_CONFIG_PATH:-${INSTALL_DIR}/config.json}"
fi
if [[ -z "${ENV_FILE}" ]]; then
  ENV_FILE="${DETECTED_ENV_FILE:-/etc/default/${SERVICE_NAME}}"
fi
if [[ -z "${LOG_LEVEL}" ]]; then
  LOG_LEVEL="${DETECTED_LOG_LEVEL:-INFO}"
fi
if [[ -z "${FORCE_DRY_RUN}" ]]; then
  FORCE_DRY_RUN="${DETECTED_DRY_RUN:-0}"
fi

INSTALL_DIR_REAL="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "${INSTALL_DIR}")"
CONFIG_PATH_REAL="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "${CONFIG_PATH}")"

echo "Deploy target"
echo "  service: ${SERVICE_NAME}"
echo "  install dir: ${INSTALL_DIR_REAL}"
echo "  config path: ${CONFIG_PATH_REAL}"
echo "  user/group: ${SERVICE_USER}:${SERVICE_GROUP}"
echo "  env file: ${ENV_FILE}"
echo "  log level: ${LOG_LEVEL}"
echo "  dry-run: ${FORCE_DRY_RUN}"
echo "  dashboard service: ${DASHBOARD_SERVICE_NAME}"
if [[ -n "${DETECTED_LOAD_STATE}" ]]; then
  echo "  detected service load state: ${DETECTED_LOAD_STATE}"
fi

if [[ "${CONFIG_PATH_REAL}" != "${INSTALL_DIR_REAL}/config.json" ]]; then
  echo "Syncing local config.json to external runtime config: ${CONFIG_PATH_REAL}"
  sudo install -d -m 755 "$(dirname "${CONFIG_PATH_REAL}")"
  sudo install -m 600 "${LOCAL_CONFIG_PATH}" "${CONFIG_PATH_REAL}"
fi

deploy_cmd=(
  sudo
  bash
  "${INSTALL_SCRIPT}"
  --service-name "${SERVICE_NAME}"
  --user "${SERVICE_USER}"
  --group "${SERVICE_GROUP}"
  --install-dir "${INSTALL_DIR_REAL}"
  --config "${CONFIG_PATH_REAL}"
  --env-file "${ENV_FILE}"
  --log-level "${LOG_LEVEL}"
)

if [[ "${FORCE_DRY_RUN}" == "1" ]]; then
  deploy_cmd+=(--dry-run)
fi

echo "Running deploy/install script..."
"${deploy_cmd[@]}"

DASHBOARD_LOAD_STATE=""
if [[ "${SKIP_DASHBOARD_RESTART}" -eq 0 && -n "${DASHBOARD_SERVICE_NAME}" ]]; then
  DASHBOARD_LOAD_STATE="$(
    systemctl show "${DASHBOARD_SERVICE_NAME}" --property=LoadState --value --no-page 2>/dev/null || true
  )"
  if [[ "${DASHBOARD_LOAD_STATE}" == "loaded" ]]; then
    echo
    echo "Restarting dashboard service: ${DASHBOARD_SERVICE_NAME}"
    sudo systemctl restart "${DASHBOARD_SERVICE_NAME}"
  else
    echo
    echo "Dashboard service not loaded; skipping restart: ${DASHBOARD_SERVICE_NAME}"
  fi
fi

if [[ "${SKIP_STATUS}" -eq 0 ]]; then
  echo
  echo "Current service status:"
  sudo systemctl --no-pager --full status "${SERVICE_NAME}" || true
  if [[ "${DASHBOARD_LOAD_STATE}" == "loaded" ]]; then
    echo
    echo "Current dashboard status:"
    sudo systemctl --no-pager --full status "${DASHBOARD_SERVICE_NAME}" || true
  fi
  echo
  echo "Recent journal lines:"
  sudo journalctl -u "${SERVICE_NAME}" -n 20 --no-pager || true
fi
