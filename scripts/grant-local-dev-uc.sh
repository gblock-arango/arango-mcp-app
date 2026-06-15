#!/usr/bin/env bash
# Ensure UC tables exist and grant SELECT/MODIFY to the local_dev identity (CLI user).
# Mirrors cloud deploy_app.sh Genie + registry grants, but targets the developer instead of the app SP.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=scripts/load-app-yaml-env.sh
source "${SCRIPT_DIR}/load-app-yaml-env.sh"

if [[ -x "${ROOT}/.venv/bin/python3" ]]; then
  PYTHON_BIN="${ROOT}/.venv/bin/python3"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
  PYTHON_BIN="${VIRTUAL_ENV}/bin/python3"
else
  PYTHON_BIN="python3"
fi
export PYTHON_BIN

PROFILE="${DATABRICKS_CONFIG_PROFILE:-}"
if [[ -n "${PROFILE}" ]]; then
  export PROFILE
  PROFILE_ARGS=(--profile "${PROFILE}")
else
  PROFILE_ARGS=()
fi
export PROFILE_ARGS

WAREHOUSE_ID="${DATABRICKS_SQL_WAREHOUSE_ID:-}"
if [[ -z "${WAREHOUSE_ID// }" ]]; then
  echo "ERROR: DATABRICKS_SQL_WAREHOUSE_ID is required for local_dev UC grants (set in app.yaml or shell)." >&2
  exit 1
fi

# shellcheck source=scripts/_databricks_sql_lib.sh
source "${SCRIPT_DIR}/_databricks_sql_lib.sh"

GENIE_SPACE_REGISTRY_TABLE="${GENIE_SPACE_REGISTRY_TABLE:-workspace.default.genie_space_registry}"
REGISTRY_TABLE="${ARANGO_REGISTRY_TABLE:-workspace.default.arango_connection_registry}"
ARANGO_GATEWAY_REGISTRY_TABLE="${ARANGO_GATEWAY_REGISTRY_TABLE:-workspace.default.arango_gateway_registry}"
ARANGO_AGENT_REGISTRY_TABLE="${ARANGO_AGENT_REGISTRY_TABLE:-workspace.default.arango_agent_registry}"
ARANGO_WORKFLOW_REGISTRY_TABLE="${ARANGO_WORKFLOW_REGISTRY_TABLE:-workspace.default.arango_workflow_registry}"

resolve_cli_user() {
  local name="" json=""
  if [[ -n "${LOCAL_DEV_UC_GRANTEE:-}" ]]; then
    echo "${LOCAL_DEV_UC_GRANTEE}"
    return 0
  fi
  if [[ -n "${GENIE_REGISTRY_DEPLOY_GRANTEE:-}" ]]; then
    echo "${GENIE_REGISTRY_DEPLOY_GRANTEE}"
    return 0
  fi
  name="$(
    PYTHONPATH="${ROOT}/src" "${PYTHON_BIN}" -c "
import sys
try:
    from databricks.sdk import WorkspaceClient
    me = WorkspaceClient().current_user.me()
    print((me.user_name or '').strip())
except Exception:
    sys.exit(1)
" 2>/dev/null
  )" || true
  if [[ -n "${name}" ]]; then
    echo "${name}"
    return 0
  fi
  json="$(databricks current-user me -o json "${PROFILE_ARGS[@]}" 2>/dev/null || echo '{}')"
  name="$("${PYTHON_BIN}" -c 'import json,sys; d=json.load(sys.stdin); print((d.get("userName") or d.get("user_name") or "").strip())' <<< "${json}" 2>/dev/null || true)"
  if [[ -n "${name}" ]]; then
    echo "${name}"
    return 0
  fi
  json="$(databricks api get /api/2.0/preview/users/me "${PROFILE_ARGS[@]}" 2>/dev/null || echo '{}')"
  name="$("${PYTHON_BIN}" -c 'import json,sys; d=json.load(sys.stdin); print((d.get("userName") or d.get("user_name") or "").strip())' <<< "${json}" 2>/dev/null || true)"
  if [[ -n "${name}" ]]; then
    echo "${name}"
    return 0
  fi
  return 1
}

CLI_USER="$(resolve_cli_user)" || {
  echo "ERROR: could not resolve CLI user (run 'databricks auth login' or set LOCAL_DEV_UC_GRANTEE)." >&2
  exit 1
}
GRANTEE="\`${CLI_USER}\`"
echo "local_dev UC grants for user '${CLI_USER}' (warehouse ${WAREHOUSE_ID})"

_failures=0
_run_grant() {
  local desc="$1"
  local sql="$2"
  local required="${3:-optional}"
  echo "==> ${desc}"
  if run_sql_statement "${sql}"; then
    return 0
  fi
  echo "WARNING: ${desc} failed — ${sql}" >&2
  if [[ "${required}" == "required" ]]; then
    _failures=$((_failures + 1))
  fi
  return 1
}

_grant_catalog_schema() {
  local catalog="$1"
  local schema="$2"
  _run_grant "USE CATALOG on ${catalog}" \
    "GRANT USE CATALOG ON CATALOG ${catalog} TO ${GRANTEE}" required || true
  _run_grant "USE SCHEMA on ${catalog}.${schema}" \
    "GRANT USE SCHEMA ON SCHEMA ${catalog}.${schema} TO ${GRANTEE}" required || true
}

_grant_catalog_schema "workspace" "default"

if [[ -n "${GENIE_SPACE_REGISTRY_TABLE}" ]]; then
  GENIE_REG_CATALOG="$(echo "${GENIE_SPACE_REGISTRY_TABLE}" | cut -d. -f1)"
  GENIE_REG_SCHEMA="$(echo "${GENIE_SPACE_REGISTRY_TABLE}" | cut -d. -f2)"
  if [[ -n "${GENIE_REG_CATALOG}" && -n "${GENIE_REG_SCHEMA}" ]]; then
    echo "Ensuring Genie registry table ${GENIE_SPACE_REGISTRY_TABLE}..."
    _ge_ensure=(env)
    if [[ -n "${PROFILE}" ]]; then
      _ge_ensure+=("DATABRICKS_CONFIG_PROFILE=${PROFILE}")
    fi
    _ge_ensure+=(-u DATABRICKS_CLIENT_ID -u DATABRICKS_CLIENT_SECRET)
    _ge_ensure+=(PYTHONPATH="${ROOT}/src" "${PYTHON_BIN}" -c "
import sys
sys.path.insert(0, '${ROOT}/src')
from arango_mcp.config import genie_cli_config_dict
from arango_dashboard_agent.services.genie_registry import ensure_genie_registry_table
cfg = genie_cli_config_dict()
ensure_genie_registry_table(cfg['GENIE_SPACE_REGISTRY_TABLE'], cfg['DATABRICKS_SQL_WAREHOUSE_ID'])
")
    "${_ge_ensure[@]}"

    if [[ "${GENIE_REG_CATALOG}.${GENIE_REG_SCHEMA}" != "workspace.default" ]]; then
      _grant_catalog_schema "${GENIE_REG_CATALOG}" "${GENIE_REG_SCHEMA}"
    fi
    _run_grant "SELECT, MODIFY on ${GENIE_SPACE_REGISTRY_TABLE}" \
      "GRANT SELECT, MODIFY ON TABLE ${GENIE_SPACE_REGISTRY_TABLE} TO ${GRANTEE}" required || true
  fi
fi

for tbl in \
  "${REGISTRY_TABLE}" \
  "${ARANGO_GATEWAY_REGISTRY_TABLE}" \
  "${ARANGO_AGENT_REGISTRY_TABLE}" \
  "${ARANGO_WORKFLOW_REGISTRY_TABLE}"; do
  [[ -n "${tbl// }" ]] || continue
  _run_grant "SELECT on ${tbl}" \
    "GRANT SELECT ON TABLE ${tbl} TO ${GRANTEE}" optional || true
done

if [[ "${_failures}" -gt 0 ]]; then
  echo "ERROR: ${_failures} required local_dev UC grant(s) failed." >&2
  exit 1
fi

echo "local_dev UC grants complete."
