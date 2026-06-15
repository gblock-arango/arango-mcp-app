#!/usr/bin/env bash
# Local mcp-app on http://127.0.0.1:8002
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/load-app-yaml-env.sh
source "${ROOT}/scripts/load-app-yaml-env.sh"

export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
export PORT="${LOCAL_MCP_PORT:-8002}"
export DATABRICKS_APP_PORT="${PORT}"

GUNICORN="${ROOT}/.venv/bin/gunicorn"
if [[ ! -x "${GUNICORN}" ]]; then
  echo "error: missing ${ROOT}/.venv — run: ./scripts/build-local.sh" >&2
  exit 1
fi

echo "==> arango-mcp-app http://127.0.0.1:${PORT}"
cd "${ROOT}"
exec "${GUNICORN}" asgi:app -k uvicorn.workers.UvicornWorker --bind "127.0.0.1:${PORT}" --workers 1 --timeout 660
