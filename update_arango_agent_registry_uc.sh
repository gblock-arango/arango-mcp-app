#!/usr/bin/env bash
set -euo pipefail

# Publish the arango-mcp-app public HTTPS URL to Unity Catalog (for arango-dashboard-app
# and other consumers). Run as a human with SQL warehouse access. Optionally pass the
# agent app service principal id so the running app can upsert the same table on startup.
#
# Usage:
#   ./update_arango_agent_registry_uc.sh [base-url] [app-name] [table] [warehouse-id] [profile] [agent-sp-client-id]
#
# Optional env: AGENT_REGISTRY_UC_UPSERT_RETRIES (default 10) — retries on concurrent Delta writes.

BASE_URL_INPUT="${1:-${DATABRICKS_APP_URL:-}}"
APP_NAME_INPUT="${2:-${DATABRICKS_APP_NAME:-mcp-arango-agent}}"
REGISTRY_TABLE="${3:-${ARANGO_AGENT_REGISTRY_TABLE:-workspace.default.arango_agent_registry}}"
WAREHOUSE_ID="${4:-${DATABRICKS_SQL_WAREHOUSE_ID:-}}"
PROFILE="${5:-}"
AGENT_SP_ID="${6:-${APP_SERVICE_PRINCIPAL_CLIENT_ID:-}}"

if [[ -z "${BASE_URL_INPUT}" ]]; then
  echo "ERROR: agent base URL required (arg1 or DATABRICKS_APP_URL)." >&2
  exit 1
fi

if [[ -z "${WAREHOUSE_ID// }" ]]; then
  echo "ERROR: SQL warehouse id required (arg4 or DATABRICKS_SQL_WAREHOUSE_ID)." >&2
  exit 1
fi

BASE_URL="${BASE_URL_INPUT%/}"

if [[ -n "${PROFILE}" ]]; then
  PROFILE_ARGS=(--profile "${PROFILE}")
else
  PROFILE_ARGS=()
fi

IFS='.' read -r CATALOG_NAME SCHEMA_NAME TABLE_NAME <<< "${REGISTRY_TABLE}"
if [[ -z "${CATALOG_NAME:-}" || -z "${SCHEMA_NAME:-}" || -z "${TABLE_NAME:-}" ]]; then
  echo "ERROR: REGISTRY_TABLE must be catalog.schema.table" >&2
  exit 1
fi

safe_sql_literal() {
  printf "%s" "$1" | sed "s/'/''/g"
}

run_sql() {
  local statement="$1"
  local payload
  payload="$(
    python3 -c 'import json,sys; print(json.dumps({"warehouse_id":sys.argv[1], "statement":sys.argv[2], "wait_timeout":"30s"}))' \
      "${WAREHOUSE_ID}" "${statement}"
  )"

  local response
  response="$(databricks api post /api/2.0/sql/statements --json "${payload}" "${PROFILE_ARGS[@]}")"

  local status statement_id
  status="$(python3 -c 'import json,sys; print((json.load(sys.stdin).get("status") or {}).get("state",""))' <<< "${response}")"
  statement_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("statement_id",""))' <<< "${response}")"

  if [[ -z "${statement_id}" ]]; then
    echo "ERROR: SQL statement did not return statement_id" >&2
    echo "${response}" >&2
    return 1
  fi

  for _ in $(seq 1 30); do
    if [[ "${status}" == "SUCCEEDED" ]]; then
      return 0
    fi
    if [[ "${status}" == "FAILED" || "${status}" == "CANCELED" || "${status}" == "CLOSED" ]]; then
      echo "ERROR: SQL statement ${statement_id} status=${status}" >&2
      databricks api get "/api/2.0/sql/statements/${statement_id}" "${PROFILE_ARGS[@]}" >&2 || true
      return 1
    fi
    sleep 1
    response="$(databricks api get "/api/2.0/sql/statements/${statement_id}" "${PROFILE_ARGS[@]}")"
    status="$(python3 -c 'import json,sys; print((json.load(sys.stdin).get("status") or {}).get("state",""))' <<< "${response}")"
  done

  echo "ERROR: SQL statement ${statement_id} did not finish in time." >&2
  return 1
}

ESC_URL="$(safe_sql_literal "${BASE_URL}")"
ESC_APP="$(safe_sql_literal "${APP_NAME_INPUT}")"
FQTBL="\`${CATALOG_NAME}\`.\`${SCHEMA_NAME}\`.\`${TABLE_NAME}\`"

echo "Ensuring arango-mcp-app URL registry schema/table exists..."
run_sql "CREATE SCHEMA IF NOT EXISTS \`${CATALOG_NAME}\`.\`${SCHEMA_NAME}\`" || exit 1
run_sql "CREATE TABLE IF NOT EXISTS ${FQTBL} (base_url STRING NOT NULL, app_name STRING NOT NULL, is_active BOOLEAN NOT NULL, updated_at TIMESTAMP NOT NULL) USING DELTA" || exit 1

echo "Granting SELECT, MODIFY on ${REGISTRY_TABLE} to \`account users\`..."
if ! ( run_sql "GRANT SELECT, MODIFY ON TABLE ${FQTBL} TO \`account users\`" ); then
  echo "NOTE: GRANT to \`account users\` failed (ignore if you are not table owner)." >&2
fi

echo "Upserting active arango-mcp-app base URL into ${REGISTRY_TABLE}..."
# Idempotent MERGE so concurrent writers (this script + gunicorn workers running
# publish_agent_base_url on startup) cannot leave duplicate active rows. The MERGE
# atomically (a) inserts the row when missing, (b) re-activates an existing row for
# this base_url, and (c) deactivates every other ``is_active=TRUE`` row via
# ``WHEN NOT MATCHED BY SOURCE``. Delta optimistic concurrency conflicts get retried
# below.
MERGE_SQL="MERGE INTO ${FQTBL} t USING (SELECT '${ESC_URL}' AS base_url, '${ESC_APP}' AS app_name, current_timestamp() AS updated_at) s ON t.base_url = s.base_url WHEN MATCHED THEN UPDATE SET app_name = s.app_name, is_active = TRUE, updated_at = s.updated_at WHEN NOT MATCHED THEN INSERT (base_url, app_name, is_active, updated_at) VALUES (s.base_url, s.app_name, TRUE, s.updated_at) WHEN NOT MATCHED BY SOURCE AND t.is_active = TRUE THEN UPDATE SET is_active = FALSE, updated_at = current_timestamp()"
UPSERT_ATTEMPTS="${AGENT_REGISTRY_UC_UPSERT_RETRIES:-10}"
for attempt in $(seq 1 "${UPSERT_ATTEMPTS}"); do
  if run_sql "${MERGE_SQL}"; then
    break
  fi
  if [[ "${attempt}" -ge "${UPSERT_ATTEMPTS}" ]]; then
    echo "ERROR: agent registry MERGE failed after ${UPSERT_ATTEMPTS} attempts." >&2
    exit 1
  fi
  echo "NOTE: UC MERGE conflict (often concurrent app startup); retrying (${attempt}/${UPSERT_ATTEMPTS})..." >&2
  sleep $((1 + attempt))
done

if [[ -n "${AGENT_SP_ID}" ]]; then
  echo "Granting SELECT, MODIFY on ${REGISTRY_TABLE} to agent app SP ${AGENT_SP_ID}..."
  run_sql "GRANT SELECT, MODIFY ON TABLE ${FQTBL} TO \`${AGENT_SP_ID}\`"
fi

echo "Arango-agent URL registry updated:"
echo "  base_url=${BASE_URL}"
echo "  app_name=${APP_NAME_INPUT}"
echo "  table=${REGISTRY_TABLE}"
