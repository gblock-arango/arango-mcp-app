#!/usr/bin/env bash
set -euo pipefail

# Deploy Arango MCP as a Databricks App (same UC grant pattern as arango-dashboard-app).
# Typical use: log in with the Databricks CLI, then from this directory:
#   ./deploy_app.sh
#
# Optional positional overrides: app-name, workspace source path, profile, then placeholders
#   $4–$7 (tunnel/cluster/registry/warehouse); set ``DATABRICKS_SQL_WAREHOUSE_ID`` or pass ``$7``
#   (no built-in warehouse id). Only profile + warehouse matter for UC grants.
# Gateway URL + agent URL registries: gateway table is written by arango-gateway-app; agent table
# by arango-mcp-app (this script publishes after deploy). Dashboard reads both via UC when env URLs are empty.
#
# Genie: when ``GENIE_SPACE_REGISTRY_TABLE`` is non-empty, this script ensures the UC Delta registry
# table exists and grants the **agent** app SP SELECT+MODIFY (same pattern as arango-dashboard-app).
# Space create/validate runs inside the deployed app at startup. Skip Genie UC steps:
#   GENIE_SPACE_REGISTRY_TABLE= ./deploy_app.sh
#
# On first run, if the Databricks App name does not exist yet, the script runs
# ``databricks apps create`` before ``databricks apps deploy``. A brand-new app often shows
# ``app_status=UNAVAILABLE`` until the first deploy; that is normal (see ``ensure_app_running_before_deploy``).
#
# After deploy: ``scripts/ensure_serving_endpoints.py`` (Databricks SDK) summarizes
# ``GENIEMCP_SERVING_ENDPOINT`` / ``TOOL_ROUTER_SERVING_ENDPOINT``. It does not create endpoints.
# To fail the script when an endpoint is missing or not READY: ``ENSURE_SERVING_ENDPOINTS_FAIL_DEPLOY=1``.
#
# Genie Code Custom MCP: default app name is ``mcp-arango-agent`` (must start with ``mcp-``). Post-deploy,
# ``scripts/grant_deploy_user_app_can_use.py`` PATCHes CAN_USE for the CLI user. Skip with
# ``GRANT_GENIE_CODE_DEPLOY_USER_CAN_USE=0``; set ``GRANT_GENIE_CODE_APP_CAN_USE_USER`` for a fixed email.

# App name must start with ``mcp-`` to appear under Genie Code → Add MCP Servers → Custom MCP server.
# See https://docs.databricks.com/aws/en/genie-code/mcp
APP_NAME="${1:-mcp-arango-agent}"
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
WAREHOUSE_ID="${DATABRICKS_SQL_WAREHOUSE_ID:-${7:-}}"
ARANGO_GATEWAY_REGISTRY_TABLE="${ARANGO_GATEWAY_REGISTRY_TABLE:-workspace.default.arango_gateway_registry}"
ARANGO_AGENT_REGISTRY_TABLE="${ARANGO_AGENT_REGISTRY_TABLE:-workspace.default.arango_agent_registry}"
GENIE_SPACE_REGISTRY_TABLE="${GENIE_SPACE_REGISTRY_TABLE:-workspace.default.genie_space_registry}"
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
  local json app_state compute_state app_msg
  if ! json="$(databricks apps get "${APP_NAME}" --output json "${PROFILE_ARGS[@]}" 2>/dev/null)"; then
    return 0
  fi
  app_state="$(
    "${PYTHON_BIN}" -c 'import json,sys; d=json.load(sys.stdin); print((d.get("app_status") or {}).get("state",""))' <<< "${json}" 2>/dev/null || true
  )"
  compute_state="$(
    "${PYTHON_BIN}" -c 'import json,sys; d=json.load(sys.stdin); print((d.get("compute_status") or {}).get("state",""))' <<< "${json}" 2>/dev/null || true
  )"
  app_msg="$(
    "${PYTHON_BIN}" -c 'import json,sys; d=json.load(sys.stdin); print((d.get("app_status") or {}).get("message",""))' <<< "${json}" 2>/dev/null || true
  )"
  if [[ "${app_state}" == "RUNNING" ]]; then
    echo "App '${APP_NAME}' is RUNNING; proceeding to deploy."
    return 0
  fi
  # After `apps create`, compute is often ACTIVE while app_status stays UNAVAILABLE until the first
  # `apps deploy`. Starting the app in that state only produces CLI noise; deploy succeeds anyway.
  if [[ "${app_state}" == "UNAVAILABLE" && "${compute_state}" == "ACTIVE" ]]; then
    if echo "${app_msg}" | grep -qiE 'not been deployed|deploy(ing)?[[:space:]]+source|run your app by deploying'; then
      echo "NOTE: App '${APP_NAME}' has no source deployment yet (app_status=UNAVAILABLE, compute_status=ACTIVE)."
      echo "      Skipping \`databricks apps start\`; the next step (\`databricks apps deploy\`) uploads code and should make the app available."
      return 0
    fi
  fi
  echo "App '${APP_NAME}' is not RUNNING (app_status=${app_state:-unknown}, compute_status=${compute_state:-unknown})."
  echo "Trying \`databricks apps start\` so compute is ready (deploy may still succeed if the platform accepts it)..."
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
    --description "Arango agent — MCP + HTTP (Genie); gateway-backed Arango; UC gateway URL" \
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

verify_serving_endpoint() {
  local ep="$1"
  if [[ -z "${ep// }" ]]; then
    return 0
  fi
  echo "Serving endpoint probe: '${ep}'"
  local se_json
  if ! se_json="$(databricks serving-endpoints get "${ep}" -o json "${PROFILE_ARGS[@]}" 2>/dev/null)"; then
    echo "WARNING: databricks serving-endpoints get '${ep}' failed (wrong name, region, or permissions)." >&2
    return 0
  fi
  "${PYTHON_BIN}" -c '
import json,sys
d=json.load(sys.stdin)
name=d.get("name") or ""
state=d.get("state") or {}
ready=state.get("ready") if isinstance(state,dict) else None
print(f"  endpoint={name!r} state.ready={ready!r}")
' <<< "${se_json}" || true
}

probe_mcp_diagnostics() {
  local base="$1"
  if [[ -z "${base// }" ]]; then
    return 0
  fi
  local diag="${base%/}/api/mcp/diagnostics"
  echo "MCP diagnostics: GET ${diag}"
  if [[ -n "${DATABRICKS_TOKEN:-}" ]]; then
    if curl -sS -f -H "Authorization: Bearer ${DATABRICKS_TOKEN}" "${diag}" | "${PYTHON_BIN}" -m json.tool 2>/dev/null; then
      echo "(genie_code_mcp = Genie Code /mcp; internal_full_catalog_mcp = dashboard /mcp/internal)"
    else
      echo "WARNING: MCP diagnostics request failed (token may not authorize this app URL)." >&2
    fi
  else
    echo "NOTE: DATABRICKS_TOKEN unset — skipping diagnostics curl. After login: export token or use UI."
  fi
}

verify_serving_endpoint "${GENIEMCP_SERVING_ENDPOINT:-}"
verify_serving_endpoint "${TOOL_ROUTER_SERVING_ENDPOINT:-}"
probe_mcp_diagnostics "${APP_URL}"

# Same endpoint names as above, via Databricks SDK (Serving API — not UC SQL).
if [[ -n "${PROFILE}" ]]; then
  export DATABRICKS_CONFIG_PROFILE="${PROFILE}"
fi
set +e
"${PYTHON_BIN}" "${SCRIPT_DIR}/scripts/ensure_serving_endpoints.py"
_ensure_se_rc=$?
set -e
if [[ "${_ensure_se_rc}" -ne 0 ]]; then
  echo "WARNING: SDK serving-endpoint probe reported missing or NOT_READY (dashboard MCP needs a chat-capable endpoint)." >&2
  if [[ "${ENSURE_SERVING_ENDPOINTS_FAIL_DEPLOY:-}" == "1" ]]; then
    exit 1
  fi
fi

if [[ -z "${WAREHOUSE_ID// }" ]]; then
  echo "ERROR: DATABRICKS_SQL_WAREHOUSE_ID is not set (export it, set in app.yaml, use arango-platform-bundle variables, or pass as 7th positional arg to deploy_app.sh)." >&2
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

echo "Granting SELECT on agent URL registry (${ARANGO_AGENT_REGISTRY_TABLE}) to agent app SP..."
if ! ( run_sql_statement "GRANT SELECT ON TABLE ${ARANGO_AGENT_REGISTRY_TABLE} TO \`${APP_SERVICE_PRINCIPAL_CLIENT_ID}\`" ); then
  echo "NOTE: GRANT SELECT on ${ARANGO_AGENT_REGISTRY_TABLE} failed (table may not exist yet; deploy script will upsert below)." >&2
fi

echo "Publishing arango-mcp-app app URL to Unity Catalog (${ARANGO_AGENT_REGISTRY_TABLE})..."
_publish_agent_uc_ok=0
if [[ -n "${PROFILE}" ]]; then
  if ( "${SCRIPT_DIR}/update_arango_agent_registry_uc.sh" \
    "${APP_URL}" "${APP_NAME}" "${ARANGO_AGENT_REGISTRY_TABLE}" "${WAREHOUSE_ID}" "${PROFILE}" \
    "${APP_SERVICE_PRINCIPAL_CLIENT_ID}" ); then
    _publish_agent_uc_ok=1
  fi
else
  if ( "${SCRIPT_DIR}/update_arango_agent_registry_uc.sh" \
    "${APP_URL}" "${APP_NAME}" "${ARANGO_AGENT_REGISTRY_TABLE}" "${WAREHOUSE_ID}" "" \
    "${APP_SERVICE_PRINCIPAL_CLIENT_ID}" ); then
    _publish_agent_uc_ok=1
  fi
fi
if [[ "${_publish_agent_uc_ok}" -ne 1 ]]; then
  echo "NOTE: Agent URL UC publish failed (often permissions or concurrent app startup). Restart arango-mcp-app once, then re-run ./deploy_app.sh or run update_arango_agent_registry_uc.sh manually." >&2
fi

resolve_genie_registry_deploy_grantee() {
  local name="" json=""
  if [[ -n "${GENIE_REGISTRY_DEPLOY_GRANTEE:-}" ]]; then
    echo "${GENIE_REGISTRY_DEPLOY_GRANTEE}"
    return 0
  fi
  name="$(
    PYTHONPATH="${SCRIPT_DIR}/src" "${PYTHON_BIN}" -c "
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

if [[ -n "${GENIE_SPACE_REGISTRY_TABLE}" ]]; then
  GENIE_REG_CATALOG="$(echo "${GENIE_SPACE_REGISTRY_TABLE}" | cut -d. -f1)"
  GENIE_REG_SCHEMA="$(echo "${GENIE_SPACE_REGISTRY_TABLE}" | cut -d. -f2)"
  GENIE_REG_NAME="$(echo "${GENIE_SPACE_REGISTRY_TABLE}" | cut -d. -f3)"
  if [[ -z "${GENIE_REG_CATALOG}" || -z "${GENIE_REG_SCHEMA}" || -z "${GENIE_REG_NAME}" ]]; then
    echo "ERROR: GENIE_SPACE_REGISTRY_TABLE must be catalog.schema.table (got '${GENIE_SPACE_REGISTRY_TABLE}')" >&2
    exit 1
  fi

  export GENIE_SPACE_REGISTRY_TABLE
  export DATABRICKS_SQL_WAREHOUSE_ID="${WAREHOUSE_ID}"

  echo "Ensuring Genie registry table exists (Databricks CLI / default login; app OAuth not used here)..."
  _ge_ensure=(env)
  if [[ -n "${PROFILE}" ]]; then
    _ge_ensure+=("DATABRICKS_CONFIG_PROFILE=${PROFILE}")
  fi
  _ge_ensure+=(-u DATABRICKS_CLIENT_ID -u DATABRICKS_CLIENT_SECRET PYTHONPATH="${SCRIPT_DIR}/src" "${PYTHON_BIN}" -c "
import sys
sys.path.insert(0, \"${SCRIPT_DIR}/src\")
from arango_mcp.config import genie_cli_config_dict
from arango_dashboard_agent.services.genie_registry import ensure_genie_registry_table
cfg = genie_cli_config_dict()
ensure_genie_registry_table(cfg[\"GENIE_SPACE_REGISTRY_TABLE\"], cfg[\"DATABRICKS_SQL_WAREHOUSE_ID\"])
")
  "${_ge_ensure[@]}"
  echo "NOTE: That step only creates the Delta Genie registry table (if missing)."
  echo "Genie Space create/validate runs inside the agent app at startup (app OAuth)."
  echo "Optional strict reconcile: POST ${APP_URL%/}/api/deploy/reconcile-genie with a token valid for *.databricksapps.com."

  echo "Granting UC privileges on Genie space registry table '${GENIE_SPACE_REGISTRY_TABLE}'..."
  run_sql_statement "GRANT USE CATALOG ON CATALOG ${GENIE_REG_CATALOG} TO \`${APP_SERVICE_PRINCIPAL_CLIENT_ID}\`"
  run_sql_statement "GRANT USE SCHEMA ON SCHEMA ${GENIE_REG_CATALOG}.${GENIE_REG_SCHEMA} TO \`${APP_SERVICE_PRINCIPAL_CLIENT_ID}\`"
  run_sql_statement "GRANT SELECT ON TABLE ${GENIE_SPACE_REGISTRY_TABLE} TO \`${APP_SERVICE_PRINCIPAL_CLIENT_ID}\`"
  run_sql_statement "GRANT MODIFY ON TABLE ${GENIE_SPACE_REGISTRY_TABLE} TO \`${APP_SERVICE_PRINCIPAL_CLIENT_ID}\`"

  DEPLOY_USER="$(resolve_genie_registry_deploy_grantee 2>/dev/null || true)"
  if [[ -n "${DEPLOY_USER}" ]]; then
    echo "Granting SELECT, MODIFY on Genie registry to '${DEPLOY_USER}' (for manual PAT-based provision if you use it)..."
    if ! ( run_sql_statement "GRANT SELECT, MODIFY ON TABLE ${GENIE_SPACE_REGISTRY_TABLE} TO \`${DEPLOY_USER}\`" ); then
      echo "WARNING: Could not GRANT registry table to '${DEPLOY_USER}'. If the table was created by the app service principal, ask a metastore admin to run:" >&2
      echo "  GRANT SELECT, MODIFY ON TABLE ${GENIE_SPACE_REGISTRY_TABLE} TO \`${DEPLOY_USER}\`;" >&2
    fi
  else
    echo "NOTE: Set GENIE_REGISTRY_DEPLOY_GRANTEE to your user email if a GRANT to the deploy identity is required." >&2
  fi
fi

echo
echo "DATABRICKS_APP_URL=${APP_URL}"
echo "NOTE: Genie Code MCP URL for this app: ${APP_URL%/}/mcp"
echo "NOTE: App name '${APP_NAME}' must start with mcp- to appear in Genie Code → Custom MCP server."
echo "NOTE: MCP CORS — leave MCP_CORS_ALLOW_ORIGINS empty in app.yaml; at runtime the App derives"
echo "      Allow-Origin from DATABRICKS_HOST (workspace UI). Override MCP_CORS_ALLOW_ORIGINS only for extra origins or *."

# Genie Code: grant CAN_USE on this app to the deploy user so they can select it in Custom MCP (PATCH ACL).
# Skip with GRANT_GENIE_CODE_DEPLOY_USER_CAN_USE=0. Override user with GRANT_GENIE_CODE_APP_CAN_USE_USER=email.
_grant_gc="${GRANT_GENIE_CODE_DEPLOY_USER_CAN_USE:-true}"
if [[ "${_grant_gc,,}" == "true" || "${_grant_gc}" == "1" ]]; then
  if [[ -n "${PROFILE}" ]]; then
    export DATABRICKS_CONFIG_PROFILE="${PROFILE}"
  fi
  _grant_args=(--app-name "${APP_NAME}")
  if [[ -n "${GRANT_GENIE_CODE_APP_CAN_USE_USER:-}" ]]; then
    _grant_args+=(--user "${GRANT_GENIE_CODE_APP_CAN_USE_USER}")
  fi
  if PYTHONPATH="${SCRIPT_DIR}/src" "${PYTHON_BIN}" "${SCRIPT_DIR}/scripts/grant_deploy_user_app_can_use.py" "${_grant_args[@]}"; then
    :
  else
    echo "WARNING: grant_deploy_user_app_can_use.py failed — add CAN_USE manually: Compute → Apps → ${APP_NAME} → Sharing." >&2
  fi
fi
echo "# Gateway URL is read from UC (${ARANGO_GATEWAY_REGISTRY_TABLE}) unless ARANGO_GATEWAY_BASE_URL is set."
echo "# Agent URL is read from UC (${ARANGO_AGENT_REGISTRY_TABLE}) unless ARANGO_AGENT_BASE_URL is set on consumers (e.g. dashboard)."
echo "registry table (read): ${REGISTRY_TABLE}"
echo "warehouse id: ${WAREHOUSE_ID}"
echo "NOTE: unused deploy placeholders kept for parity with arango-dashboard-app/deploy_app.sh: LOCAL_ARANGO_URL=${LOCAL_ARANGO_URL} CLUSTER_NAME=${CLUSTER_NAME}"
