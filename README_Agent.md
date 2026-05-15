# README_Agent — `arango-agent` (Databricks App)

This document is **for LLM coding agents and engineers**: a structured, implementation-grounded description of **what this app is for**, **how HTTP and MCP are layered**, **where configuration lives and how it merges from bundles**, and **how the agent cooperates with `arango-gateway-app`, `arango-dashboard-app`, and `arango-platform-bundle`**. It complements the shorter, human-oriented `README.md` in this repository.

Paths are relative to **`databricks/arango-agent/`** unless stated otherwise.

---

## 1. Role, intent, and boundaries

### 1.1 What this app owns

**`arango-agent`** is the **reasoning and integration hub** in the Arango-on-Databricks split:

- **Databricks Genie (Space)** conversational API for AI/BI-style Q&A, tied to a **Unity Catalog Delta registry** that stores the active Genie space id for **this app’s service principal**.
- **Dashboard “MCP” mode**: an **in-process** loop that calls a **workspace foundation-model serving endpoint** via the OpenAI-compatible **`/serving-endpoints`** surface and binds **FastMCP tools** from the **full** Arango catalog (the same tool implementations used for stdio MCP and for HTTP **`/mcp/internal`**).
- **Dashboard “ADA” / cluster chat mode**: optional HTTP forwarding to **`ARANGO_CONVERSATION_URL`** when that URL is configured.
- **Genie Code (workspace UI)**: a **second, smaller** Streamable HTTP MCP surface at **`/mcp`** so Genie Code’s **combined 20-tool limit** across all MCP servers is respected; coarse tools delegate to Genie, ADA, or stub graph operations.
- **Operational HTTP**: health, MCP diagnostics, gateway-backed debug probes, and post-deploy Genie registry reconciliation hooks.

### 1.2 What this app deliberately does *not* own

- **Primary ownership of Arango cluster credentials** and the **browser-facing Arango Web UI embed** live on **`arango-gateway-app`**. The agent reaches Arango through **gateway HTTP** (`POST /api/arango/http` pattern) when gateway mode is active, not by embedding the cluster password in this app’s defaults.
- **Browser chrome and same-origin dashboard `fetch`** live on **`arango-dashboard-app`**, which **reverse-proxies** Genie / MCP / ADA chat to this app so the user’s Databricks Apps **on-behalf-of-user** token can be forwarded where required.

---

## 2. Relationship to other repositories

| Artifact | Role relative to `arango-agent` |
|----------|-----------------------------------|
| **`arango-gateway-app`** | Publishes its public **`*.databricksapps.com`** base URL to **`ARANGO_GATEWAY_REGISTRY_TABLE`**. MCP tools and agent routes that need Arango or UC-backed graph import resolve **`ARANGO_GATEWAY_BASE_URL`** from that table (plus **`DATABRICKS_SQL_WAREHOUSE_ID`**) when the env override is empty. |
| **`arango-dashboard-app`** | Proxies **`POST /api/genie/chat`**, **`POST /api/genie-mcp/chat`**, **`POST /api/arango/chat`** to this app. Its **`app.yaml`** declares an **`app`** resource granting **CAN USE** on the deployed agent app name (**`mcp-arango-agent`**) so the dashboard service principal may invoke the agent. Users need **CAN USE** on peer apps for server-side `requests` between Apps. |
| **`arango-platform-bundle`** | Optional umbrella **Databricks Asset Bundle** that declares all three apps under **`resources/apps.yml`**, injects shared **`variables`** (warehouse id, UC table FQNs, optional URL overrides), and points each app’s **`source_code_path`** at the sibling repo directories. Agent env entries in the bundle **override** same-named keys from this repo’s **`app.yaml`** at deploy time. |

**Genie Code note:** Databricks only lists **Custom MCP server** apps whose **name starts with `mcp-`**. This repo’s default Databricks App name is therefore **`mcp-arango-agent`**, not `arango-agent-app`. See [Connect Genie Code to MCP servers](https://docs.databricks.com/aws/en/genie-code/mcp).

---

## 3. Runtime entrypoints: ASGI vs WSGI

| Entry | Command (typical) | Purpose |
|-------|-------------------|---------|
| **Production (Databricks App)** | `gunicorn asgi:app -k uvicorn.workers.UvicornWorker` (see `app.yaml` **`command`**) | **Starlette** mounts **Streamable HTTP MCP** at **`/mcp`** and **`/mcp/internal`**, then mounts **Flask** at **`/`** for **`/api/*`**. **Lifespan** starts **two** FastMCP `session_manager` contexts (Genie Code MCP + full catalog MCP). |
| **Local / minimal** | `gunicorn wsgi:app` (see `README.md`) | **Flask only** — useful for API debugging **without** MCP HTTP surfaces. MCP-over-HTTP will not be served. |

The canonical production object is **`asgi:app`** in **`src/asgi.py`**.

---

## 4. HTTP and MCP surface map

### 4.1 Starlette layer (`src/asgi.py`)

- **`Mount("/mcp/internal/", ...)`** — **full** FastMCP app (`arango_mcp.server.mcp_app`): on the order of **~74** tools, same registry as stdio MCP.
- **`Mount("/mcp/", ...)`** — **Genie Code** FastMCP app (`arango_mcp.genie_code_mcp.mcp_genie_code_app`): **five** coarse tools.
- **Mount order matters:** **`/mcp/internal/`** is registered **before** **`/mcp/`** so the longer prefix is not swallowed by the shorter mount.
- **CORS:** `CORSMiddleware` wraps **both** MCP mounts. If **`MCP_CORS_ALLOW_ORIGINS`** is empty, the app derives allowed origins from **`DATABRICKS_HOST`** (Databricks Apps inject this) so Genie Code running in the workspace browser origin can call **`/mcp`** without manual CORS env tuning.
- **Browser GET hint:** middleware returns JSON help when a browser does a normal GET without MCP **`Accept: text/event-stream`**.

### 4.2 Flask layer (`src/arango_agent/webapp.py`, `src/arango_agent/routes/api.py`)

Mounted under **`/api`** (see blueprint registration in `webapp.py`). Important routes:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Liveness. |
| `GET` | `/api/mcp/diagnostics` | JSON inventory: tool names/counts for **`/mcp`** vs **`/mcp/internal`**, plus manifest metadata from **`src/arango_mcp/tool_registries/*.json`**. |
| `POST` | `/api/genie/chat` | Genie Space conversation (requires **`GENIE_SPACE_ID`** in app config after UC bootstrap / auto-provision). |
| `POST` | `/api/genie-mcp/chat` | **Dashboard MCP mode**: workspace LLM + **in-process** **`mcp_app`** tool calls (full catalog, subject to **`GENIEMCP_MAX_TOOLS`** cap). |
| `POST` | `/api/arango/chat` | **ADA** forward or stub (`ARANGO_CONVERSATION_URL`). |
| `POST` | `/api/deploy/reconcile-genie` | Post-deploy UC + Genie repair hook used by `deploy_app.sh`. |
| `GET` | `/api/debug/startup-status` | Merges local Genie diagnostics with optional **gateway** `GET /api/debug/startup-status` when gateway base URL is known. |

---

## 5. Dual FastMCP design (why two servers)

### 5.1 `mcp_app` — full catalog (`arango_mcp/server.py`)

- **`FastMCP`** instance **`mcp_app`** with **`stateless_http=True`**, **`streamable_http_path="/"`**, **`TransportSecuritySettings(enable_dns_rebinding_protection=False)`** so the server operates behind Databricks ingress.
- **Tool registration** is **import side-effect**: after `mcp_app` is constructed, **`from arango_mcp.mcp_tools import …`** registers tools (see bottom of `server.py`).
- **Lifespan:** **`arango_db_lifespan`** connects Arango via **gateway** or **direct** settings (`arango_mcp/arango_connector.py`).

### 5.2 `mcp_genie_code_app` — Genie Code surface (`arango_mcp/genie_code_mcp.py`)

- Separate **`FastMCP`** with a **small** tool set in **`arango_mcp/mcp_tools/genie_code_tools.py`** (Genie Space tool, ADA tool, graph **stubs**).
- **Same** transport flags as `mcp_app` so Databricks “custom MCP App” expectations are met.
- **Genie Code clients** must use **`https://<this-app-host>/mcp`** only. Pointing Genie Code at **`/mcp/internal`** will quickly blow the **20-tool combined** budget and is not the intended product path.

### 5.3 Manifest JSON (`src/arango_mcp/tool_registries/`)

Files such as **`genie_code_manifest.json`**, **`full_catalog_manifest.json`**, and **`migration_pool_manifest.json`** are **documentation / routing metadata**, not the mechanism that registers tools. **`load_manifest()`** supplies human- and diagnostics-friendly labels. The **authoritative** tool list is always **`FastMCP._tool_manager.list_tools()`** at runtime.

---

## 6. Configuration: models, env vars, and Flask `app.config`

### 6.1 `AppSettings` and nested settings (`src/arango_mcp/config.py`)

**`AppSettings`** (Pydantic **`BaseSettings`**) is the **single aggregate** for:

- **Warehouse / UC tables** used by the agent and by gateway URL resolution helpers (`DATABRICKS_SQL_WAREHOUSE_ID`, `ARANGO_REGISTRY_TABLE`, gateway registry table, agent registry table, Genie registry table).
- **Genie** lifecycle flags and titles (`GENIE_*` keys — see `Field` descriptions; many correspond 1:1 to **`app.yaml`** env names).
- **MCP dashboard chat** (`GENIEMCP_*`, `TOOL_ROUTER_*`).
- **CORS** (`MCP_CORS_ALLOW_ORIGINS`).
- **ADA** forwarding (`ARANGO_CONVERSATION_*`).

Nested models:

- **`ArangoDBSettings`** — env prefix **`ARANGO_`**: direct Arango URLs and credentials when **not** using gateway (local MCP clients, rare App paths).
- **`GatewaySettings`** — env prefix **`ARANGO_GATEWAY_`**: optional fixed **`base_url`**, **`registry_table`**, bearer token override, TLS, timeouts.
- **`ServerSettings`** — MCP server metadata, logging, **`enable_js_transactions`** (dangerous; off by default).

**`settings`** at module bottom is the process-global instance.

### 6.2 `flask_app_config()` — bridge into Flask

**`flask_app_config(settings)`** returns an **uppercase dict** merged into **`Flask.config`** in **`create_app()`**. That is what **`genie_mcp_orchestrator`**, Genie registry helpers, and gateway URL code read via **`current_app.config`**.

**Not every `app.yaml` variable is duplicated on `AppSettings`.** Some Genie behaviors read **`os.environ` directly** inside service modules (example: **`GENIE_VERIFY_GENIE_SPACE_READABLE`** in `genie_registry.py`). When in doubt, **grep the symbol** across `src/` rather than assuming it flows through `flask_app_config`.

### 6.3 `app.yaml` (this repo)

`app.yaml` is the **Databricks Apps** manifest shipped with the repo:

- **`command`** — starts **ASGI** Gunicorn + Uvicorn worker.
- **`env`** — structured list of **`name` / `value` / `description`**. Comments group **required** vs **optional** keys.
- **`resources`** — e.g. **`sql_warehouse`** with **`permission: CAN_USE`**; `${DATABRICKS_SQL_WAREHOUSE_ID}` is expanded from env.

When you deploy **without** the parent bundle, these values are exactly what the platform injects.

---

## 7. Parent bundle: how overrides reach this app

**`arango-platform-bundle/resources/apps.yml`** defines **`arango_agent`** with:

- **`name: mcp-arango-agent`** — must stay **`mcp-`**-prefixed for Genie Code.
- **`source_code_path: ../apps/arango-agent`**
- **`config.env`** — a **subset** of variables (warehouse id, UC table names) expressed as **`${var.*}`** bundle variables.

**Merge semantics (conceptual):** at **`databricks bundle deploy`**, the platform merges the **bundle fragment** for this app with the **repo `app.yaml`**. Keys present in **`apps.yml` → config.env** typically **win** over duplicate names in the repo file for that deployment. Keys **only** in repo `app.yaml` keep their defaults unless you add them to the bundle.

**`arango-agent/resources/arango_mcp.app.yml`** exists for **bundle-style** declarations when this repo is deployed as a **standalone** bundle; keep **`name`** aligned with **`mcp-arango-agent`** unless you have a deliberate multi-app strategy.

---

## 8. Major behavioral paths (code pointers)

### 8.1 Genie Space chat (`POST /api/genie/chat`)

1. **`refresh_genie_space_id_in_app`** ensures **`GENIE_SPACE_ID`** in Flask config tracks UC + Genie API reality (`genie_registry.py`).
2. **`ask_genie_conversation`** uses **`agent_workspace_client()`** — a **`WorkspaceClient`** configured for **Databricks App OAuth** (this app’s service principal) or PAT-injection paths off-platform (`genie_workspace_client.py`).
3. ACL / space readability failures may **invalidate** UC rows and **retry** with a new space (`invalidate_genie_space_after_acl_error`).

### 8.2 Dashboard MCP mode (`POST /api/genie-mcp/chat`)

Implemented in **`genie_mcp_orchestrator.py`**:

1. Resolves **serving endpoint name** in order: **`TOOL_ROUTER_SERVING_ENDPOINT`**, then **`GENIEMCP_SERVING_ENDPOINT`**, else optional **`GENIEMCP_FOUNDATION_MODEL_QUERY`** via **`foundation_model_endpoint_resolver.py`** (Workspace **Serving API**, not UC SQL).
2. Builds **OpenAI Python client** with **`base_url = {workspace host}/serving-endpoints`** and token from **`WorkspaceClient.config.authenticate()`**.
3. Lists tools from **`mcp_app._tool_manager`** (capped by **`GENIEMCP_MAX_TOOLS`**), runs **chat.completions** with **tool_calls**, dispatches into **`mcp_app._tool_manager.call_tool`**.
4. Requires **Arango connector** to succeed for tools that touch the DB — i.e. gateway (or direct) must be viable.

### 8.3 Gateway-backed Arango (`arango_mcp/gateway_arango_client.py`, connector)

Gateway mode resolves **`ARANGO_GATEWAY_BASE_URL`** using **`gateway_resolution_config`** + SQL against **`ARANGO_GATEWAY_REGISTRY_TABLE`** (`arango_agent/services/gateway_url_registry.py` mirrors dashboard logic for the agent’s Flask side).

The MCP tool stack and Genie tools that touch Arango ultimately go through the **gateway HTTP** contract, not raw cluster TLS from the agent in the default design.

### 8.4 Agent URL publication

**`publish_self_agent_url_to_uc_if_configured`** (called from **`create_app()`**) writes the app’s public URL to **`ARANGO_AGENT_REGISTRY_TABLE`** when **`ARANGO_AGENT_REGISTRY_AUTO_CREATE=true`**, so **`arango-dashboard-app`** can resolve the agent without a static **`ARANGO_AGENT_BASE_URL`**.

---

## 9. Deploy and maintenance scripts

| Script | Role |
|--------|------|
| **`deploy_app.sh`** | Sync → **`databricks apps deploy`** → read **`url`** + service principal id → UC SQL **GRANTs** for registry tables → **`update_arango_agent_registry_uc.sh`** → optional Genie registry ensure + grants → **`scripts/ensure_serving_endpoints.py`** (SDK serving probe) → optional **`scripts/grant_deploy_user_app_can_use.py`** (**`CAN_USE`** for CLI user on **`mcp-arango-agent`**, skippable via env). |
| **`update_arango_agent_registry_uc.sh`** | SQL upsert of **`(app_name, base_url)`** into **`ARANGO_AGENT_REGISTRY_TABLE`**. |
| **`scripts/resolve_serving_endpoint_for_foundation_model.py`** | CLI helper: map a **model query string** to a **READY** serving endpoint name via SDK **`serving_endpoints.list` / `get`**. |
| **`scripts/grant_deploy_user_app_can_use.py`** | SDK **`apps.update_permissions`** PATCH for **`CAN_USE`** — prerequisite for many users to **select** the app in Genie Code after deploy. |

Positional **`deploy_app.sh`** args remain: **`APP_NAME`**, **`SOURCE_CODE_PATH`**, **`PROFILE`**, then legacy placeholders through warehouse id — see script header comments.

---

## 10. Unity Catalog tables (conceptual contract)

| Table | Writer (typical) | Reader on agent |
|-------|------------------|-----------------|
| **`ARANGO_GATEWAY_REGISTRY_TABLE`** | `arango-gateway-app` | Agent + dashboard resolve **gateway base URL**. |
| **`ARANGO_AGENT_REGISTRY_TABLE`** | This app (`publish_self…`) + deploy script | Dashboard resolves **agent base URL**. |
| **`ARANGO_REGISTRY_TABLE`** | Gateway / ops | Connection metadata; deploy grants **SELECT** to agent SP. |
| **`GENIE_SPACE_REGISTRY_TABLE`** | This app (Genie auto-provision) + deploy ensure | Stores **Genie space id** for the agent identity. |

---

## 11. Source tree map (for coders)

| Path | Responsibility |
|------|------------------|
| **`src/asgi.py`** | Starlette: MCP mounts, CORS, lifespan, Flask WSGI bridge. |
| **`src/wsgi.py`** | Flask-only entry (no MCP HTTP). |
| **`src/arango_agent/webapp.py`** | Flask factory, **`app.config.from_mapping(flask_app_config())`**, blueprints, startup hooks. |
| **`src/arango_agent/routes/api.py`** | HTTP API surface described in §4.2. |
| **`src/arango_agent/services/`** | Genie, MCP orchestration, gateway URL, agent URL registry, SQL helpers, startup debug. |
| **`src/arango_mcp/server.py`** | Full FastMCP + tool import fan-in. |
| **`src/arango_mcp/genie_code_mcp.py`** | Genie Code FastMCP shell. |
| **`src/arango_mcp/mcp_tools/`** | Tool implementations; **`genie_code_tools.py`** is the Genie Code subset. |
| **`src/arango_mcp/config.py`** | **Central typed configuration** and **`flask_app_config`**. |
| **`src/arango_mcp/arango_connector.py`** | DB connection lifecycle for tools. |
| **`src/arango_mcp/tool_registries/`** | JSON manifests for diagnostics / docs. |

---

## 12. Extension points and sharp edges

1. **Tool count vs Genie Code:** never register dozens of tools on **`/mcp`**. Add coarse tools to **`genie_code_tools.py`** only when they fit the **20-tool workspace budget** across **all** MCP servers Genie Code enables.
2. **Serving endpoint strings:** **`GENIEMCP_SERVING_ENDPOINT`** values are **endpoint names** passed as OpenAI **`model`**, not full URLs; base URL is always **`{DATABRICKS_HOST}/serving-endpoints`**.
3. **`mcp-` app naming:** renaming breaks dashboard **`app.yaml`** **`CAN USE`** unless you update the **`app`** resource **`name`** and redeploy the dashboard.
4. **Permissions PATCH:** `grant_deploy_user_app_can_use.py` uses **`update_permissions`**. Workspace ACL semantics belong to the platform; verify in **Compute → Apps → Sharing** if anything looks off.
5. **Stdio MCP:** `python -m arango_mcp.main` uses **`mcp_app`** only; Genie Code surface is HTTP-only.

---

## 13. Canonical external references

- [Connect Genie Code to MCP servers](https://docs.databricks.com/aws/en/genie-code/mcp) — Agent mode, **Custom MCP server**, **`mcp-`** naming, **`/mcp`** URL.
- [Host a custom MCP server](https://docs.databricks.com/aws/en/generative-ai/mcp/custom-mcp) — Streamable HTTP, **stateless** MCP, Apps permissions.
- [Databricks Apps authentication](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth) — why the dashboard forwards user tokens for peer App calls.

When you change runtime behavior, update **`README.md`** for operators and **this file** for agents so both audiences stay aligned.
