#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${VENV_DIR:-$repo_root/venv}"
config_path="${1:-$repo_root/config/qcii.yaml}"

if [[ ! -f "$venv_dir/bin/activate" ]]; then
    echo "error: virtual environment not found at $venv_dir" >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$venv_dir/bin/activate"
exec python -m qcii_detector.cli run --config "$config_path"
