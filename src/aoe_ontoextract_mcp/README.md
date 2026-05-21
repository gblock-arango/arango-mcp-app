# AOE OntoExtract MCP (`/mcp/aoe`)

Ontology-extraction MCP tools for **mcp-arango-agent**. Mounted at **`/mcp/aoe/`** in `asgi.py`.

## No shared code with workflow-app

Tools call **arango-workflow-app** over HTTPS via the public BFF prefix
``/api/workflow/ontoextract/v1/*`` (same peer-app auth model as dashboard→agent:
Databricks app OAuth + optional inbound user bearer). Do not add `arango-workflow-app/src`
to `PYTHONPATH`.

| Env (mcp-app) | Written by | Purpose |
|---------------|------------|---------|
| `ARANGO_WORKFLOW_APP_BASE_URL` | — | Optional override |
| `ARANGO_WORKFLOW_REGISTRY_TABLE` | **workflow-app** startup + deploy script | Active workflow `base_url` |

Grant **CAN USE** on `arango-workflow-app` in mcp-app `app.yaml` (`arango-workflow-app-invoke`).

## Tools (HTTP bridge)

- `aoe_workflow_health` — `GET /api/workflow/health`
- `aoe_list_ontology_library` — BFF → `GET /api/v1/ontology/library`
- `aoe_get_ontology_registry_entry` — BFF → `GET /api/v1/ontology/library/{id}`
- `aoe_workflow_api` — generic BFF REST escape hatch

Legacy modules under `tools/` and `resources/` (direct `app.db` imports) are **not** registered.

## Verify

```bash
curl -sS "$MCP_APP_URL/api/mcp/diagnostics" | jq '.aoe_ontoextract_mcp'
./scripts/read_uc_peer_registry.sh   # workflow-app, after deploy
```
