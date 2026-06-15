#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${DATABRICKS_APP_PORT:-8000}"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec gunicorn asgi:app -k uvicorn.workers.UvicornWorker --bind "0.0.0.0:${PORT}" --workers 2 --timeout 660
