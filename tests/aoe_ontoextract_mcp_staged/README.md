# Staged AOE MCP tests

Copied from `arango-workflow-app` when OntoExtract MCP was removed from the workflow app.

**Not collected by default.** Imports expect `app.mcp` and a full OntoExtract `app` package on `PYTHONPATH`.

To run manually (from repo root, with workflow-app or ontoextract backend on path):

```bash
PYTHONPATH=src:../arango-workflow-app/src pytest tests/aoe_ontoextract_mcp_staged/ -m "not integration and not e2e"
```
