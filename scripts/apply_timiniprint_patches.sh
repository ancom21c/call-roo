#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SUBMODULE_PATH="${REPO_ROOT}/vendor/TiMini-Print"
PATCH_DIR="${REPO_ROOT}/patches/timiniprint"

git_safe() {
  git \
    -c safe.directory="${REPO_ROOT}" \
    -c safe.directory="${SUBMODULE_PATH}" \
    "$@"
}

if ! command -v git >/dev/null 2>&1; then
  echo "git not found." >&2
  exit 1
fi

git_safe -C "${REPO_ROOT}" submodule update --init --recursive vendor/TiMini-Print

if [[ ! -d "${PATCH_DIR}" ]]; then
  exit 0
fi

shopt -s nullglob
patches=("${PATCH_DIR}"/*.patch)

for patch_path in "${patches[@]}"; do
  patch_name="$(basename -- "${patch_path}")"

  if git_safe -C "${SUBMODULE_PATH}" apply --reverse --check "${patch_path}" >/dev/null 2>&1; then
    echo "TiMini-Print patch already applied: ${patch_name}"
    continue
  fi

  if ! git_safe -C "${SUBMODULE_PATH}" apply --check "${patch_path}" >/dev/null 2>&1; then
    echo "Failed to apply TiMini-Print patch: ${patch_name}" >&2
    echo "Make sure vendor/TiMini-Print is at the checked-in submodule commit and does not contain conflicting local edits." >&2
    exit 1
  fi

  git_safe -C "${SUBMODULE_PATH}" apply "${patch_path}"
  echo "Applied TiMini-Print patch: ${patch_name}"
done
