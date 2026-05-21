"""AOE OntoExtract MCP — HTTP bridge to arango-workflow-app at ``/mcp/aoe`` on mcp-arango-agent."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from aoe_ontoextract_mcp.tools_http import register_http_tools

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

_instructions = """
AOE (Arango-OntoExtract) MCP — ontology extraction and curation via **arango-workflow-app**.

Tools call workflow ``/api/workflow/ontoextract/v1/*`` over HTTPS (peer BFF; same app OAuth model as
dashboard→agent). Resolve workflow URL from ``ARANGO_WORKFLOW_REGISTRY_TABLE`` (workflow publishes on startup).

For raw Arango introspection use this app's ``/mcp/internal`` catalog instead.
"""

mcp_aoe_app = FastMCP(
    name="AOE OntoExtract",
    instructions=_instructions,
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

register_http_tools(mcp_aoe_app)

log.info("AOE MCP (HTTP bridge) configured tool_count=%s", len(mcp_aoe_app._tool_manager.list_tools()))
