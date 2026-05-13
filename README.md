Responsibility: Tool-calling reasoning over Arango/UC/Vector Search
Databricks App: an MCP-enabled agent that enables Databricks services to support ArangoAI Chat, perform tool-calling and reasoning over ArangoDB (AQL for graph & vector search), Unity Catalog, etc.
Comments: Calls gateway tools/endpoints.
This should be an agent endpoint/tool-calling service, not the owner of deterministic data movement:
- Natural-language query handling
- Tool selection
- GraphRAG explanation
- MCP tool calls
- Arango semantic query planning
- Analyst-facing reasoning

## ArangoDB MCP (this repository)

The Databricks MCP server, **gateway-backed Arango access**, and Databricks App metadata live at this **repository root** (alongside the `arango-solutions-mcp/` reference submodule). **Apache-2.0** — see `LICENSE`.

**Gateway mode:** optional `ARANGO_GATEWAY_BASE_URL`; otherwise resolve the active URL from `ARANGO_GATEWAY_REGISTRY_TABLE` using `DATABRICKS_SQL_WAREHOUSE_ID` (same pattern as `arango-dashboard-app`). `ARANGO_GATEWAY_BEARER_TOKEN` is optional. **Deploy / UC grants:** run `./deploy_app.sh` from this directory (includes Genie registry table + app SP grants when `GENIE_SPACE_REGISTRY_TABLE` is set).

**Genie (UC registry / shell provision, not the app SP):** from this repo root, `./update_genie_registry_uc.sh` or `PYTHONPATH=src python src/provision_genie_uc.py` — see script headers. **`./update_arango_agent_registry_uc.sh`** publishes this app’s URL to **`ARANGO_AGENT_REGISTRY_TABLE`** (same idea as the gateway’s UC script). The Databricks App also upserts that table on startup when **`ARANGO_AGENT_REGISTRY_AUTO_CREATE=true`**.

**HTTP entry (Databricks App):** `gunicorn asgi:app -k uvicorn.workers.UvicornWorker` (see `app.yaml`). This serves **stateless Streamable HTTP MCP** at **`https://<app-host>/mcp`** for **Genie Code** (Agent mode) plus the existing Flask routes (`/api/...`, `/health`). Pure Flask (no MCP HTTP) remains available as `gunicorn wsgi:app` for local debugging.

**Genie Code + custom MCP:** In the workspace, open **Genie Code** → **Agent mode** → add a **Custom MCP server** and select this deployed app. Databricks requires the MCP endpoint at **`/mcp`** and a **stateless** HTTP MCP server (this app matches that contract). Opening **`/mcp` in a normal browser tab** is not the MCP protocol (GET must advertise **`Accept: text/event-stream`**); the app returns a short JSON hint instead of a JSON-RPC error. If the browser reports CORS errors, set **`MCP_CORS_ALLOW_ORIGINS`** on the app to your workspace origin (for example `https://<workspace-host>.cloud.databricks.com`), comma-separated if you need several. Official reference: [Connect Genie Code to MCP servers](https://docs.databricks.com/aws/en/genie-code/mcp).

---

# ArangoDB MCP Server

A comprehensive [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server for ArangoDB, providing **74 tools** covering document CRUD, graph traversals, AQL queries, vector/semantic search, cluster administration, stream transactions, hot backup, user/permission management, and more.

Built for AI assistants (Cursor, Claude Desktop, etc.) that need full-spectrum access to ArangoDB's multi-model capabilities.

## Supported ArangoDB Versions

- **ArangoDB 3.12+** (vector search requires 3.12.4+ with `--vector-index`)
- **ArangoDB 4.0** (under development — forward-compatible)

## Prerequisites

- Python 3.10+
- [Poetry](https://python-poetry.org/docs/#installation) for dependency management
- ArangoDB instance (local, Docker, or remote)

## Quick Start

### 1. Install

```bash
git clone https://github.com/arango-solutions/arango-solutions-mcp.git
cd arango-solutions-mcp
poetry install
```

### 2. Configure Your MCP Client

**Cursor IDE** — edit `.cursor/mcp.json` (set `cwd` to this repository root):

```json
{
  "mcpServers": {
    "arangodb-mcp": {
      "command": "poetry",
      "args": ["run", "python", "-m", "arango_mcp.main"],
      "cwd": "/path/to/arango-agent",
      "env": {
        "PYTHONPATH": "src",
        "ARANGO_HOSTS": "http://localhost:8529",
        "ARANGO_ROOT_USERNAME": "root",
        "ARANGO_ROOT_PASSWORD": "your_password_here",
        "ARANGO_DEFAULT_DB_NAME": "myapp"
      }
    }
  }
}
```

**Claude Desktop** — edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "arangodb-mcp": {
      "command": "poetry",
      "args": ["run", "python", "-m", "arango_mcp.main"],
      "cwd": "/path/to/arango-agent",
      "env": {
        "PYTHONPATH": "src",
        "ARANGO_HOSTS": "http://localhost:8529",
        "ARANGO_ROOT_USERNAME": "root",
        "ARANGO_ROOT_PASSWORD": "your_password_here",
        "ARANGO_DEFAULT_DB_NAME": "myapp"
      }
    }
  }
}
```

### 3. Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ARANGO_HOSTS` | Yes | — | ArangoDB server URL(s) |
| `ARANGO_ROOT_USERNAME` | Yes | — | ArangoDB username |
| `ARANGO_ROOT_PASSWORD` | Yes | — | ArangoDB password |
| `ARANGO_DEFAULT_DB_NAME` | No | `_system` | Default database name |
| `ARANGO_VERIFY_SSL` | No | `true` | Verify SSL certificates |
| `ARANGO_SSL_CERT_PATH` | No | — | Path to SSL certificate file |
| `LOG_LEVEL` | No | `INFO` | Server log level |
| `ENABLE_JS_TRANSACTIONS` | No | `false` | Enable server-side JavaScript transactions (security-sensitive) |

#### Databricks + `arango-gateway-app`

Optional `ARANGO_GATEWAY_BASE_URL`; otherwise the active row in `ARANGO_GATEWAY_REGISTRY_TABLE` read with `DATABRICKS_SQL_WAREHOUSE_ID` via the Databricks SDK (same as `arango-dashboard-app`). `ARANGO_GATEWAY_BEARER_TOKEN` is optional. Repo layout: `app.yaml`, `databricks.yml`, `deploy_app.sh`, `resources/` at repo root; Python packages under `src/arango_mcp/` (MCP + Arango) and `src/arango_agent/` (HTTP app, Genie, UC helpers).

| Variable | When | Description |
|----------|------|-------------|
| `DATABRICKS_SQL_WAREHOUSE_ID` | Required for UC gateway URL resolution | Warehouse id for reading `ARANGO_GATEWAY_REGISTRY_TABLE`; set via `export`, `app.yaml`, or **arango-platform-bundle** `sql_warehouse_id` |
| `ARANGO_GATEWAY_REGISTRY_TABLE` | Optional | Defaults to `workspace.default.arango_gateway_registry` |
| `ARANGO_REGISTRY_TABLE` | Optional | Defaults to `workspace.default.arango_connection_registry` (deploy grants / future reads) |
| `ARANGO_GATEWAY_BEARER_TOKEN` | Rare | Only if gateway ingress requires Bearer |
| `MCP_CORS_ALLOW_ORIGINS` | Optional | Comma-separated origins for CORS on `/mcp` (Genie Code). Example: `https://my-workspace.cloud.databricks.com`. Use `*` for permissive dev (no credentials). Empty disables CORS middleware. |

All tools accept an optional `database_name` parameter to override the default.

---

## Tools (74)

### Document Operations (10)

| Tool | Description |
|------|-------------|
| `create-document` | Insert a single document |
| `create-documents-bulk` | Bulk insert an array of documents |
| `read-document` | Get a document by key or ID |
| `read-documents-with-filter` | Query documents with filters, pagination |
| `update-document` | Partial update by key |
| `delete-document` | Remove a document by key |
| `replace-document` | Full document replacement |
| `upsert-document` | Insert or update based on search criteria |
| `update-documents-bulk` | Bulk partial updates via AQL |
| `delete-documents-bulk` | Bulk deletes via AQL filter |

### Collection Management (4)

| Tool | Description |
|------|-------------|
| `list-collections` | List all collections in a database |
| `create-collection` | Create document/edge collections (with sharding, replication, computed values) |
| `delete-collection` | Drop a collection |
| `get-collection-properties` | Collection stats, shard config, key type |

### Database Management (4)

| Tool | Description |
|------|-------------|
| `list-databases` | List all databases |
| `create-database` | Create a new database |
| `delete-database` | Drop a database |
| `get-database-info` | Database properties and stats |

### Graph Management (5)

| Tool | Description |
|------|-------------|
| `list-graphs` | List named graphs |
| `create-graph` | Create graphs (standard, SmartGraph, SatelliteGraph, EnterpriseGraph) |
| `delete-graph` | Drop a graph (optionally drop collections) |
| `create-edge` | Insert an edge between two vertices |
| `get-graph-properties` | Edge definitions, orphan collections, cluster config |

### Graph Traversals (4)

| Tool | Description |
|------|-------------|
| `graph-traverse` | Multi-depth traversal with vertex/edge filters, path return |
| `graph-shortest-path` | Single shortest path (optionally weighted) |
| `graph-k-shortest-paths` | K alternative shortest paths |
| `graph-neighbors` | Deduplicated neighbor discovery at a given depth |

### AQL Query Engine (3)

| Tool | Description |
|------|-------------|
| `execute-aql-query` | Execute AQL with bind variables, stats |
| `explain-aql-query` | Execution plan analysis (indexes, costs, optimizer rules) |
| `validate-aql-query` | Syntax check without execution |

### Index Management (3)

| Tool | Description |
|------|-------------|
| `list-indexes` | List indexes on a collection |
| `create-index` | Create persistent, inverted, geo, TTL, vector (ANN), MDI indexes |
| `delete-index` | Remove an index by ID |

### Vector & Semantic Search (2)

*Requires ArangoDB 3.12.4+ with `--vector-index` enabled.*

| Tool | Description |
|------|-------------|
| `vector-search` | Approximate nearest-neighbor search (cosine, L2, inner product) |
| `hybrid-search` | Combined vector + BM25 text search with weighted fusion |

### Search Views (6)

| Tool | Description |
|------|-------------|
| `list-views` | List ArangoSearch / search-alias views |
| `create-view` | Create an ArangoSearch or search-alias view |
| `get-view-properties` | View configuration details |
| `update-view-properties` | Modify view settings (partial) |
| `replace-view-properties` | Replace view configuration |
| `delete-view` | Drop a view |

### Analyzers (4)

| Tool | Description |
|------|-------------|
| `list-analyzers` | List text analyzers |
| `create-analyzer` | Create a custom analyzer (text, ngram, stem, etc.) |
| `delete-analyzer` | Remove an analyzer |
| `get-analyzer-properties` | Analyzer type and configuration |

### Cluster Administration (9)

| Tool | Description |
|------|-------------|
| `cluster-health` | Overall cluster health status |
| `cluster-server-role` | Role of the connected server (Coordinator, DBServer, Single) |
| `cluster-server-count` | Number of coordinators + DB servers |
| `cluster-endpoints` | List all coordinator endpoints |
| `cluster-server-statistics` | CPU, memory, request stats for a server |
| `cluster-calculate-imbalance` | Shard distribution imbalance report |
| `cluster-rebalance` | Trigger automatic shard rebalancing |
| `cluster-toggle-maintenance` | Enable/disable cluster maintenance mode |
| `collection-shard-distribution` | Shard → server mapping for a collection |

### Stream Transactions (6)

| Tool | Description |
|------|-------------|
| `begin-transaction` | Start a stream transaction (declare read/write/exclusive collections) |
| `transaction-status` | Check if a transaction is running, committed, or aborted |
| `commit-transaction` | Commit and persist all changes |
| `abort-transaction` | Roll back all changes |
| `list-transactions` | List currently running stream transactions |
| `execute-transaction` | Execute a server-side JS transaction atomically (**disabled by default** — set `ENABLE_JS_TRANSACTIONS=true`) |

### Hot Backup (4) — Enterprise Edition

| Tool | Description |
|------|-------------|
| `create-backup` | Create a point-in-time hot backup of the deployment |
| `list-backups` | List available backups |
| `restore-backup` | Restore from a backup (server restarts) |
| `delete-backup` | Permanently remove a backup |

### User & Permission Management (9)

| Tool | Description |
|------|-------------|
| `list-users` | List all server users |
| `get-user` | Get user details and metadata |
| `create-user` | Create a new user with password, active flag, extra data |
| `update-user` | Update password, active status, or metadata |
| `delete-user` | Remove a user and all permission grants |
| `list-permissions` | All database/collection permission grants for a user |
| `get-permission` | Effective permission level on a database or collection |
| `grant-permission` | Grant rw/ro/none access at database or collection level |
| `revoke-permission` | Remove a permission grant (falls back to parent level) |

### AQL Reference (1)

| Tool | Description |
|------|-------------|
| `get-aql-manual` | Retrieve AQL syntax, optimization, or Cypher→AQL migration guides |

---

## Architecture

Layout mirrors **arango-gateway-app** (Databricks bundle + app metadata at repo root; Python under `src/`).

```
arango-agent/   (this repository — MCP server at root)
├── app.yaml
├── databricks.yml
├── deploy_app.sh
├── update_genie_registry_uc.sh
├── update_arango_agent_registry_uc.sh
├── requirements.txt
├── pyproject.toml
├── resources/
│   └── arango_mcp.app.yml
├── src/
│   ├── app.py                 # local stdio MCP: PYTHONPATH=src python src/app.py
│   ├── provision_genie_uc.py  # Genie UC shell provision
│   ├── wsgi.py
│   ├── arango_agent/          # Databricks App (HTTP/Genie), UC/SQL — not MCP tools
│   │   ├── webapp.py
│   │   ├── routes/
│   │   └── services/
│   └── arango_mcp/            # MCP + Arango tool/agent code only
│       ├── main.py
│       ├── server.py
│       ├── config.py
│       ├── arango_connector.py
│       ├── gateway_arango_client.py
│       ├── gateway_database.py
│       ├── aql_utils.py
│       ├── agents/
│       ├── mcp_tools/
│       └── manuals/
├── tests/
└── arango-solutions-mcp/    # upstream reference submodule (optional)
```

The codebase follows a two-layer pattern:

- **MCP Tools** (`src/arango_mcp/mcp_tools/`) — thin FastMCP-decorated functions that validate inputs and delegate to agents.
- **Agents** (`src/arango_mcp/agents/`) — business logic classes inheriting from `ArangoAgentBase` (direct `python-arango` or gateway HTTP).
- **Databricks App** (`src/arango_agent/`) — Flask HTTP (Genie), UC helpers (gateway + **agent** URL registries, Genie), gateway URL resolution; separate from MCP tool modules so additional MCP packages can coexist.

---

## Development

### Running Tests

Tests automatically spin up a Docker container with ArangoDB on a random port, run the suite, and tear it down:

```bash
poetry install --with dev
poetry run pytest tests/ -v --ignore=tests/test_cluster.py
```

To test against an existing ArangoDB instance (skips Docker):

```bash
ARANGO_HOSTS=http://localhost:8529 \
ARANGO_ROOT_PASSWORD=your_password \
  poetry run pytest tests/ -v
```

Cluster-specific tests require a multi-server deployment:

```bash
poetry run pytest tests/test_cluster.py -v
```

### Linting & Formatting

```bash
poetry run ruff check .         # lint
poetry run ruff format --check . # format check (use without --check to auto-fix)
poetry run mypy --ignore-missing-imports src/arango_mcp
```

### Pre-commit Hooks

The project includes a `.pre-commit-config.yaml` for automated checks on commit:

```bash
pip install pre-commit
pre-commit install
```

### Adding a New Tool

1. Create an agent in `src/arango_mcp/agents/` inheriting from `ArangoAgentBase`
2. Create tool definitions in `src/arango_mcp/mcp_tools/` using `@mcp_app.tool`
3. Import the new tool module in `src/arango_mcp/mcp_tools/__init__.py` and `src/arango_mcp/server.py`
4. Add tests in `tests/`

---

## Security

| Feature | Description |
|---------|-------------|
| **Zero hardcoded credentials** | All connection parameters via `ARANGO_*` environment variables or `.env`; validated by Pydantic settings |
| **AQL injection prevention** | All identifiers validated by `aql_utils.py` before interpolation; all values use bind variables (`@param`) |
| **JS transaction gating** | `execute-transaction` disabled by default; requires explicit `ENABLE_JS_TRANSACTIONS=true` to enable arbitrary JS execution on the server |
| **SSL by default** | `ARANGO_VERIFY_SSL` defaults to `true`; optional `ARANGO_SSL_CERT_PATH` for custom certs |
| **Log redaction** | Bind variable values are never logged; only parameter keys appear in log output |
| **Destructive operation guards** | `_system` database deletion blocked at both tool and agent levels |

---

## Key Features

- **Zero hardcoded config** — all credentials via environment variables or `.env`
- **Multi-model coverage** — documents, graphs, search, vectors in one server
- **Cluster-aware** — sharding, replication, SmartGraphs, shard rebalancing
- **ACID transactions** — stream transactions for multi-document atomicity
- **Vector search** — approximate nearest-neighbor with cosine/L2/inner-product metrics
- **Hybrid search** — combine vector similarity with BM25 text relevance
- **AQL-first** — built-in manuals, explain plans, and syntax validation
- **Security by default** — AQL injection prevention, JS transaction gating, SSL verification, log redaction
- **Self-testing** — 223 automated tests with ephemeral Docker containers
- **Cross-platform** — runs on macOS, Linux, Windows (via Docker)

## License

See [LICENSE](LICENSE) for details.
