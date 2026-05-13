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

## `databricks-arango-mcp/`

Working tree copied from `arango-solutions-mcp/` (rsync, excludes `.git` and caches). Use this package for the Databricks fork: Arango access via `arango-gateway-app` HTTP instead of `python-arango` in `arango_connector.py` / agents. License remains **Apache-2.0** (`databricks-arango-mcp/LICENSE`). Re-sync from the submodule when you want to pull upstream MCP changes, then re-apply gateway-specific edits.

**Gateway mode:** set `ARANGO_GATEWAY_BASE_URL` (and optional `ARANGO_GATEWAY_BEARER_TOKEN` for Databricks Apps auth). `get_db()` / `get_system_db()` return a **GatewayDatabase** proxy (`gateway_database.py`) that mirrors the usual `python-arango` call patterns over `POST {gateway}/api/arango/http`. `GatewayArangoClient` is only the low-level HTTP transport. Direct `python-arango` still works when the gateway URL is unset and `ARANGO_HOSTS` / credentials are set.