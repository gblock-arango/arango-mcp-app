#!/usr/bin/env bash
set -euo pipefail

# Genie registry upsert (manual / CI — uses shell M2M or PAT, not the Databricks App service principal).
# Run from this repository (arango-mcp-app) root.
#
# Auth (first match wins):
#   1) OAuth M2M: DATABRICKS_HOST + DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET
#      (script unsets profile and PAT so the SDK uses only the confidential client).
#   2) Personal access token: DATABRICKS_HOST + DATABRICKS_TOKEN (no client secret).
#   3) Otherwise: default databricks CLI / SDK auth; optional 3rd arg is a config profile name.
#
# Usage: ./update_genie_registry_uc.sh [catalog.schema.table] [warehouse-id] [profile]

REGISTRY_TABLE="${1:-${GENIE_SPACE_REGISTRY_TABLE:-}}"
WAREHOUSE_ID="${2:-${DATABRICKS_SQL_WAREHOUSE_ID:-}}"
PROFILE="${3:-}"

if [[ -z "${REGISTRY_TABLE}" || -z "${WAREHOUSE_ID}" ]]; then
  echo "ERROR: registry table (arg1 or GENIE_SPACE_REGISTRY_TABLE) and warehouse id (arg2 or DATABRICKS_SQL_WAREHOUSE_ID) are required." >&2
  exit 1
fi

export GENIE_SPACE_REGISTRY_TABLE="${REGISTRY_TABLE}"
export DATABRICKS_SQL_WAREHOUSE_ID="${WAREHOUSE_ID}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${PYTHON_BIN:-}" && -x "${PYTHON_BIN}" ]]; then
  PYTHON="${PYTHON_BIN}"
elif [[ -x "${SCRIPT_DIR}/.venv/bin/python3" ]]; then
  PYTHON="${SCRIPT_DIR}/.venv/bin/python3"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
  PYTHON="${VIRTUAL_ENV}/bin/python3"
else
  PYTHON="python3"
fi

export PYTHONPATH="${SCRIPT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

if [[ -n "${DATABRICKS_CLIENT_ID:-}" && -n "${DATABRICKS_CLIENT_SECRET:-}" ]]; then
  if [[ -z "${DATABRICKS_HOST:-}" ]]; then
    echo "ERROR: DATABRICKS_HOST must be set when DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET are set (OAuth M2M for Genie provision)." >&2
    exit 1
  fi
  echo "Genie provision: OAuth M2M (unset DATABRICKS_CONFIG_PROFILE and DATABRICKS_TOKEN for SDK)." >&2
  exec env -u DATABRICKS_CONFIG_PROFILE -u DATABRICKS_TOKEN \
    "${PYTHON}" "${SCRIPT_DIR}/src/provision_genie_uc.py"
fi

if [[ -n "${DATABRICKS_HOST:-}" && -n "${DATABRICKS_TOKEN:-}" ]]; then
  echo "Genie provision: DATABRICKS_HOST + DATABRICKS_TOKEN (personal access token)." >&2
  if [[ -n "${PROFILE}" ]]; then
    export DATABRICKS_CONFIG_PROFILE="${PROFILE}"
  fi
  exec env -u DATABRICKS_CLIENT_ID -u DATABRICKS_CLIENT_SECRET \
    "${PYTHON}" "${SCRIPT_DIR}/src/provision_genie_uc.py"
fi

if [[ -n "${PROFILE}" ]]; then
  export DATABRICKS_CONFIG_PROFILE="${PROFILE}"
fi
exec "${PYTHON}" "${SCRIPT_DIR}/src/provision_genie_uc.py"
