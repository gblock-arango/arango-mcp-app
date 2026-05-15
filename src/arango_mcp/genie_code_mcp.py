"""Genie Code MCP surface: small tool set (≤ Databricks 20-tool cap) at ``/mcp``.

The full Arango tool catalog (74 tools) is served separately at ``/mcp/internal`` from
:class:`arango_mcp.server.mcp_app` (stdio + dashboard ``/api/genie-mcp/chat`` orchestration).

Databricks Genie Code expects Streamable HTTP MCP at ``https://<app>/mcp`` — that URL maps here.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from arango_mcp.arango_connector import arango_db_lifespan

_instructions = """
Arango agent — **Genie Code** tool surface (coarse operations).

Use these tools instead of dozens of low-level Arango tools. For fine-grained Arango MCP
(graph, AQL, indexes, …), use a client pointed at this same app's ``/mcp/internal`` path
(dashboard / ToolRouter), not Genie Code's 20-tool combined limit.

**Tools:**
- ``genie-space-conversation`` — Databricks Genie Space (AI/BI) Q&A for this app's space.
- ``ada-conversation`` — Cluster ADA / forwarded conversation when configured.
- ``create-arango-graph`` / ``search-arango-graph`` / ``upsert-arango-graph`` — graph ops (stubs
  evolving toward gateway-backed flows).
"""

mcp_genie_code_app = FastMCP(
    name="Arango Agent (Genie Code)",
    instructions=_instructions,
    lifespan=arango_db_lifespan,
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# Side-effect: registers tools on ``mcp_genie_code_app`` (must run after instance exists).
from arango_mcp.mcp_tools import genie_code_tools  # noqa: E402, F401
