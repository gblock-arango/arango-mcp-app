#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_YAML="${ROOT}/app.yaml"
PY="${PYTHON_BIN:-python3}"
SCRIPT="${ROOT}/scripts/read_app_yaml_env.py"

if [[ -f "${APP_YAML}" ]]; then
  while IFS= read -r line; do
    name="${line#export }"
    key="${name%%=*}"
    if [[ -z "${!key:-}" ]]; then
      eval "${line}"
    fi
  done < <("${PY}" "${SCRIPT}" --export-all "${APP_YAML}")
fi
