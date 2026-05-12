Responsibility: Tool-calling reasoning over Arango/UC/Vector Search
Databricks Agent: an MCP-enabled Databricks Agent that enables Databricks services to support ArangoAI Chat, perform tool-calling and reasoning over ArangoDB (AQL for graph & vector search), Unity Catalog, etc.
Comments: Calls gateway tools/endpoints.
This should be an agent endpoint/tool-calling service, not the owner of deterministic data movement:
- Natural-language query handling
- Tool selection
- GraphRAG explanation
- MCP tool calls
- Arango semantic query planning
- Analyst-facing reasoning