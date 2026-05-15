"""Genie Code MCP surface: small tool set (≤ Databricks 20-tool cap) at ``/mcp``.

The full Arango tool catalog (74 tools) is served separately at ``/mcp/internal`` from
:class:`arango_mcp.server.mcp_app` (stdio + dashboard ``/api/genie-mcp/chat`` orchestration).

Databricks Genie Code expects Streamable HTTP MCP at ``https://<app>/mcp`` — that URL maps here.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from arango_mcp.arango_connector import arango_db_lifespan

logger = logging.getLogger(__name__)

_instructions = """
Arango agent — **Genie Code** tool surface (3 tools; respects the workspace 20-tool MCP cap).

**Tools:**
- ``arango-graph-machine-learning`` — GraphML on Gold-table graphlets (stub until gateway job exists).
- ``arango-ada-conversation`` — AMP ADA via **arango-gateway-app** ``/api/arango/chat``.
- ``arango-graph-queries`` — workspace LLM + full internal MCP catalog (dashboard MCP mode).

Fine-grained MCP without Genie Code: this app's ``/mcp/internal`` or dashboard ``POST /api/genie-mcp/chat``.
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

from arango_mcp.config import settings  # noqa: E402
from arango_mcp.tool_registries import genie_code_allowed_tool_names  # noqa: E402


def apply_genie_code_tool_cap(max_tools: int | None = None) -> list[str]:
    """Expose only ``genie_code_manifest`` tools (≤ ``GENIEMCP_MAX_TOOLS``) on ``/mcp``."""
    cap = int(max_tools if max_tools is not None else settings.geniemcp_max_tools)
    allowed = set(genie_code_allowed_tool_names(max_tools=cap))
    for tool in list(mcp_genie_code_app._tool_manager.list_tools()):
        if tool.name not in allowed:
            mcp_genie_code_app.remove_tool(tool.name)
    remaining = [t.name for t in mcp_genie_code_app._tool_manager.list_tools()]
    missing = sorted(allowed - set(remaining))
    if missing:
        logger.warning("Genie Code manifest lists tools not registered on /mcp: %s", missing)
    return remaining


apply_genie_code_tool_cap()
