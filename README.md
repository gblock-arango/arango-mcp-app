# Arango MCP app (Databricks App)

**Arango MCP app** (`arango-mcp-app`) is a Databricks App that exposes **Genie** chat (Genie Spaces), **MCP** (Genie Code for inside Databricks Workspaces, HTTP for Databricks LLMs, and optional stdio for local runs), and **gateway-backed** Arango tools. 

Additional GraphML and Databricks Pipelines/Jobs are discussed in the Arango-Databricks suite of github repos.

The **dashboard** (`arango-dashboard-app`) proxies chat to this app; **Arango cluster credentials** live on **`arango-gateway-app`**. 

This repo holds `app.yaml`, `deploy_app.sh`, and Python under `src/` (`arango_dashboard_agent/` = Flask HTTP, `arango_mcp/` = MCP + tools).

---

## Quick start (deploy to Databricks)

Prerequisites:

- [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/) logged into your workspace (`databricks auth login` or a profile).
- **`DATABRICKS_SQL_WAREHOUSE_ID`** set in the environment or passed as the **7th** argument to `deploy_app.sh` (see script header). Replace the placeholder in `app.yaml` if you copy this repo fresh.
- **Gateway** deployed once so **`ARANGO_GATEWAY_REGISTRY_TABLE`** has a row (unless you set **`ARANGO_GATEWAY_BASE_URL`** manually).

From this directory:

```bash
export DATABRICKS_SQL_WAREHOUSE_ID='your-warehouse-id-hex'
./deploy_app.sh
```

Defaults: app name **`mcp-arango-agent`** (must start with **`mcp-`** so it appears under [Genie Code → Custom MCP server](https://docs.databricks.com/aws/en/genie-code/mcp)), workspace sync path derived from `databricks current-user me`.

At the end of a successful run you should see **`DATABRICKS_APP_URL=...`**, a **Genie Code MCP URL** line (`…/mcp`), and (unless disabled) a **CAN_USE** grant for your CLI user on the app. Skip the grant with **`GRANT_GENIE_CODE_DEPLOY_USER_CAN_USE=0`** if you manage sharing only in the UI.

**Optional checks**

```bash
databricks serving-endpoints get databricks-meta-llama-3-3-70b-instruct -o json   # or your GENIEMCP_SERVING_ENDPOINT
curl -sS -H "Authorization: Bearer $DATABRICKS_TOKEN" "${APP_URL}/api/mcp/diagnostics" | python3 -m json.tool
```

---

## What runs where

| Surface | URL / command | Who uses it |
|---------|----------------|-------------|
| **Flask API** | `https://<app-host>/api/...` | **Dashboard** proxies `genie/chat`, `genie-mcp/chat`, `arango/chat`. |
| **Genie Code MCP** | `https://<app-host>/mcp` | Workspace Genie Code (**~5 tools**; Databricks **20 tools total** across MCP servers). |
| **Full MCP (HTTP)** | `https://<app-host>/mcp/internal` | MCP clients that need the **full** Arango tool catalog. |
| **Stdio MCP (local)** | `PYTHONPATH=src python -m arango_mcp.main` | Cursor, Claude Desktop, etc. |

Production command is in **`app.yaml`**: `gunicorn asgi:app` with a **Uvicorn** worker so **both** MCP mounts and Flask stay up. For Flask-only local debugging: `gunicorn wsgi:app` (no MCP HTTP).

---

## Configuration (short)

Most settings live in **`app.yaml`** and become **process environment variables** in the Databricks App (same idea as “env” on a server — not “your laptop OS” unless you run locally).

| Topic | Where | Notes |
|--------|--------|--------|
| **Warehouse + UC** | `DATABRICKS_SQL_WAREHOUSE_ID`, registry table envs | Required for UC SQL (gateway URL, Genie registry, deploy grants). |
| **Dashboard MCP chat** | `GENIEMCP_SERVING_ENDPOINT`, optional `TOOL_ROUTER_SERVING_ENDPOINT` | Value is a **serving endpoint name**, not a full URL; base URL is `{DATABRICKS_HOST}/serving-endpoints`. Optional `GENIEMCP_FOUNDATION_MODEL_QUERY` resolves a READY endpoint via SDK if the two endpoints are unset. |
| **Genie space** | `GENIE_*` in `app.yaml` | Registry + auto-provision; see `README_Agent.md` §8.1. |
| **CORS for `/mcp`** | `MCP_CORS_ALLOW_ORIGINS` | Leave **empty** to auto-allow the workspace origin from **`DATABRICKS_HOST`** (Databricks Apps). |
| **Parent bundle** | `arango-platform-bundle/resources/apps.yml` | Overrides same-named `env` keys when you deploy via the bundle. |

`app.yaml` groups **required** vs **optional** keys with comments.

---

## Genie Code (pick the app once)

1. Deploy this app (**name must start with `mcp-`** — default **`mcp-arango-agent`**).
2. In the workspace: Genie Code → **Settings** → **Add Server** → **Custom MCP server** → choose **`mcp-arango-agent`** → **Save** ([docs](https://docs.databricks.com/aws/en/genie-code/mcp)).

`./deploy_app.sh` tries to grant **CAN_USE** to the CLI user so the app appears for you; other users need **CAN_USE** on the app in **Compute → Apps → Sharing**.

---

## Local development

Install (Poetry or your usual venv):

```bash
poetry install --with dev
```

**Tests** (often need a running Arango; many tests use Docker — see existing pytest layout):

```bash
poetry run pytest tests/ -v --ignore=tests/test_cluster.py
```

**Lint**

```bash
poetry run ruff check .
poetry run ruff format --check .
```

**MCP client (stdio)** — point `cwd` at this repo and set `PYTHONPATH=src`, plus `ARANGO_*` for direct mode or gateway envs for Databricks-style gateway mode (see `src/arango_mcp/config.py`).

---

## Scripts (cheat sheet)

| Script | Purpose |
|--------|---------|
| **`./deploy_app.sh`** | Sync, create/deploy app, UC grants, agent URL publish, Genie registry steps, serving probe, optional **CAN_USE** grant. |
| **`./update_arango_agent_registry_uc.sh`** | Upsert this app’s public URL into **`ARANGO_AGENT_REGISTRY_TABLE`**. |
| **`scripts/grant_deploy_user_app_can_use.py`** | Grant **CAN_USE** on the app to a user (used by deploy by default). |
| **`scripts/ensure_serving_endpoints.py`** | Print **READY** / foundation-model hints for configured serving endpoints. |
| **`scripts/resolve_serving_endpoint_for_foundation_model.py`** | Resolve a model query string to a serving **endpoint name**. |

---

## More

- **`README_Agent.md`** — long-form guide for LLM agents: dual MCP, `asgi.py`, bundle merge, UC tables, orchestrator flow.
- **`deploy_app.sh`** — read the header comment for positional args, `GENIE_SPACE_REGISTRY_TABLE=`, and failure modes.
