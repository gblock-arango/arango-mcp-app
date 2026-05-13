#!/usr/bin/env bash
set -euo pipefail

# Deploy Arango MCP as a Databricks App (same UC grant pattern as arango-dashboard-app).
# Typical use: log in with the Databricks CLI, then from this directory:
#   ./deploy_app.sh
#
# Optional positional overrides: app-name, workspace source path, profile, then placeholders
#   $4–$7 (tunnel/cluster/registry/warehouse); only profile and warehouse matter for UC grants.
# Gateway URL + connection registry rows are maintained by arango-gateway-app.
#
# On first run, if the Databricks App name does not exist yet, the script runs
# ``databricks apps create`` before ``databricks apps deploy``.

APP_NAME="${1:-arango-mcp-app}"
PROFILE="${3:-}"

_resolve_ws_user() {
  local args=() user_json user
  [[ -n "${PROFILE}" ]] && args=(--profile "${PROFILE}")
  user_json="$(databricks current-user me "${args[@]}" 2>/dev/null)" || return 1
  user="$(printf '%s' "${user_json}" | python3 -c 'import json,sys; d=json.load(sys.stdin); e=d.get("emails") or []; print(d.get("userName") or (e[0].get("value") if e else ""))' 2>/dev/null)" || return 1
  [[ -n "${user}" ]] || return 1
  printf '%s' "${user}"
}

if [[ -n "${2:-}" ]]; then
  SOURCE_CODE_PATH="$2"
else
  _ws_user="$(_resolve_ws_user)" || {
    echo "ERROR: could not resolve workspace user via 'databricks current-user me'." >&2
    echo "Pass an explicit source path: ./deploy_app.sh ${APP_NAME} /Workspace/Users/<you>/${APP_NAME}" >&2
    exit 1
  }
  SOURCE_CODE_PATH="/Workspace/Users/${_ws_user}/${APP_NAME}"
fi

LOCAL_ARANGO_URL="${4:-https://127.0.0.1:18529}"
CLUSTER_NAME="${5:-local-minikube-dev}"
REGISTRY_TABLE="${ARANGO_REGISTRY_TABLE:-${6:-workspace.default.arango_connection_registry}}"
WAREHOUSE_ID="${DATABRICKS_SQL_WAREHOUSE_ID:-${7:-473d40703241ee4c}}"
ARANGO_GATEWAY_REGISTRY_TABLE="${ARANGO_GATEWAY_REGISTRY_TABLE:-workspace.default.arango_gateway_registry}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "${SCRIPT_DIR}/.venv/bin/python3" ]]; then
  PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python3"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
  PYTHON_BIN="${VIRTUAL_ENV}/bin/python3"
else
  PYTHON_BIN="python3"
fi
export PYTHON_BIN

echo "NOTE: Arango cluster credentials live on arango-gateway-app; this script does not write ARANGO_* secrets."

if [[ -n "${PROFILE}" ]]; then
  PROFILE_ARGS=(--profile "${PROFILE}")
else
  PROFILE_ARGS=()
fi

ensure_app_running_before_deploy() {
  local json app_state compute_state
  if ! json="$(databricks apps get "${APP_NAME}" --output json "${PROFILE_ARGS[@]}" 2>/dev/null)"; then
    return 0
  fi
  app_state="$(
    "${PYTHON_BIN}" -c 'import json,sys; d=json.load(sys.stdin); print((d.get("app_status") or {}).get("state",""))' <<< "${json}" 2>/dev/null || true
  )"
  compute_state="$(
    "${PYTHON_BIN}" -c 'import json,sys; d=json.load(sys.stdin); print((d.get("compute_status") or {}).get("state",""))' <<< "${json}" 2>/dev/null || true
  )"
  if [[ "${app_state}" == "RUNNING" ]]; then
    echo "App '${APP_NAME}' is RUNNING; proceeding to deploy."
    return 0
  fi
  echo "App '${APP_NAME}' is not RUNNING (app_status=${app_state:-unknown}, compute_status=${compute_state:-unknown})."
  echo "Deploy requires RUNNING; starting app (waits until compute is active)..."
  if [[ "${SKIP_APPS_START_BEFORE_DEPLOY:-}" == "1" ]]; then
    echo "SKIP_APPS_START_BEFORE_DEPLOY=1: skipping databricks apps start; deploy may fail." >&2
    return 0
  fi
  databricks apps start "${APP_NAME}" "${PROFILE_ARGS[@]}"
}

echo "Syncing local project to '${SOURCE_CODE_PATH}'..."
databricks sync . "${SOURCE_CODE_PATH}" "${PROFILE_ARGS[@]}"

if ! databricks apps get "${APP_NAME}" "${PROFILE_ARGS[@]}" &>/dev/null; then
  echo "Creating Databricks App '${APP_NAME}' (not found in workspace; first-time setup)..."
  databricks apps create "${APP_NAME}" \
    --description "Arango MCP — tools via arango-gateway-app; UC gateway URL like dashboard" \
    "${PROFILE_ARGS[@]}"
fi

ensure_app_running_before_deploy

echo "Deploying app '${APP_NAME}' from '${SOURCE_CODE_PATH}'..."
databricks apps deploy "${APP_NAME}" \
  --source-code-path "${SOURCE_CODE_PATH}" \
  "${PROFILE_ARGS[@]}"

echo "Fetching app metadata..."
APP_JSON="$(databricks apps get "${APP_NAME}" --output json "${PROFILE_ARGS[@]}")"

APP_URL="$(
  "${PYTHON_BIN}" -c 'import json,sys; print(json.load(sys.stdin).get("url",""))' <<< "${APP_JSON}"
)"
APP_SERVICE_PRINCIPAL_CLIENT_ID="$(
  "${PYTHON_BIN}" -c 'import json,sys; print(json.load(sys.stdin).get("service_principal_client_id",""))' <<< "${APP_JSON}"
)"

if [[ -z "${APP_URL}" ]]; then
  echo "ERROR: Could not extract URL from Databricks app metadata." >&2
  exit 1
fi
if [[ -z "${APP_SERVICE_PRINCIPAL_CLIENT_ID}" ]]; then
  echo "ERROR: Could not extract app service principal client id." >&2
  exit 1
fi

run_sql_statement() {
  local statement="$1"
  local payload
  payload="$(
    "${PYTHON_BIN}" -c 'import json,sys; print(json.dumps({"warehouse_id":sys.argv[1], "statement":sys.argv[2], "wait_timeout":"30s"}))' \
      "${WAREHOUSE_ID}" "${statement}"
  )"

  local response statement_id status
  response="$(databricks api post /api/2.0/sql/statements --json "${payload}" "${PROFILE_ARGS[@]}")"
  statement_id="$("${PYTHON_BIN}" -c 'import json,sys; print(json.load(sys.stdin).get("statement_id",""))' <<< "${response}")"
  status="$("${PYTHON_BIN}" -c 'import json,sys; print((json.load(sys.stdin).get("status") or {}).get("state",""))' <<< "${response}")"

  if [[ -z "${statement_id}" ]]; then
    echo "ERROR: SQL statement did not return statement_id" >&2
    echo "${response}" >&2
    exit 1
  fi

  for _ in $(seq 1 30); do
    if [[ "${status}" == "SUCCEEDED" ]]; then
      return 0
    fi
    if [[ "${status}" == "FAILED" || "${status}" == "CANCELED" || "${status}" == "CLOSED" ]]; then
      echo "ERROR: SQL statement ${statement_id} status=${status}" >&2
      databricks api get "/api/2.0/sql/statements/${statement_id}" "${PROFILE_ARGS[@]}" >&2 || true
      exit 1
    fi
    sleep 1
    response="$(databricks api get "/api/2.0/sql/statements/${statement_id}" "${PROFILE_ARGS[@]}")"
    status="$("${PYTHON_BIN}" -c 'import json,sys; print((json.load(sys.stdin).get("status") or {}).get("state",""))' <<< "${response}")"
  done

  echo "ERROR: SQL statement ${statement_id} did not finish in time." >&2
  exit 1
}

echo "Granting UC privileges to app service principal client id '${APP_SERVICE_PRINCIPAL_CLIENT_ID}'..."
run_sql_statement "GRANT USE CATALOG ON CATALOG workspace TO \`${APP_SERVICE_PRINCIPAL_CLIENT_ID}\`"
run_sql_statement "GRANT USE SCHEMA ON SCHEMA workspace.default TO \`${APP_SERVICE_PRINCIPAL_CLIENT_ID}\`"
# Read-only on Arango connection registry (same as dashboard; writes go through the gateway app).
run_sql_statement "GRANT SELECT ON TABLE ${REGISTRY_TABLE} TO \`${APP_SERVICE_PRINCIPAL_CLIENT_ID}\`"

echo "Granting SELECT on gateway URL registry (${ARANGO_GATEWAY_REGISTRY_TABLE}) to MCP app SP..."
if ! ( run_sql_statement "GRANT SELECT ON TABLE ${ARANGO_GATEWAY_REGISTRY_TABLE} TO \`${APP_SERVICE_PRINCIPAL_CLIENT_ID}\`" ); then
  echo "NOTE: GRANT on ${ARANGO_GATEWAY_REGISTRY_TABLE} failed (create it by deploying arango-gateway-app once). MCP will resolve gateway URL after the table exists." >&2
fi

echo
echo "DATABRICKS_APP_URL=${APP_URL}"
echo "# Gateway URL is read from UC (${ARANGO_GATEWAY_REGISTRY_TABLE}) unless ARANGO_GATEWAY_BASE_URL is set."
echo "registry table (read): ${REGISTRY_TABLE}"
echo "warehouse id: ${WAREHOUSE_ID}"
echo "NOTE: unused deploy placeholders kept for parity with arango-dashboard-app/deploy_app.sh: LOCAL_ARANGO_URL=${LOCAL_ARANGO_URL} CLUSTER_NAME=${CLUSTER_NAME}"
