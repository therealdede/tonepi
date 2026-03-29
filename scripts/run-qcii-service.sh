#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_path="${1:-$repo_root/config/qcii.yaml}"
exec "$repo_root/scripts/run-qcii.sh" run --config "$config_path"
