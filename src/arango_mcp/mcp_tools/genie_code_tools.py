"""Five coarse MCP tools for Genie Code (``/mcp``). Full catalog lives on ``/mcp/internal``."""

from __future__ import annotations

import json
import logging
from typing import Any

import anyio
from pydantic import Field

from arango_agent.services.arango_conversation import ask_arango_conversation
from arango_agent.services.genie_conversation import ask_genie_conversation
from arango_agent.services.genie_registry import resolve_genie_space_id_for_app
from arango_agent.services.genie_workspace_client import agent_workspace_client
from arango_mcp.config import flask_app_config, settings
from arango_mcp.genie_code_mcp import mcp_genie_code_app

logger = logging.getLogger(__name__)


def _agent_config() -> dict[str, Any]:
    return flask_app_config(settings)


async def _genie_space_call(*, content: str, conversation_id: str | None) -> dict[str, Any]:
    cfg = _agent_config()
    space_id = resolve_genie_space_id_for_app(cfg).strip()
    if not space_id:
        return {
            "ok": False,
            "error": "Genie space id is not configured (GENIE_SPACE_ID or UC registry + warehouse).",
        }
    timeout = float(cfg.get("GENIE_MESSAGE_TIMEOUT_SECONDS") or 600.0)

    def _call() -> dict[str, Any]:
        return ask_genie_conversation(
            workspace_client=agent_workspace_client(),
            space_id=space_id,
            content=content,
            conversation_id=conversation_id,
            timeout_seconds=timeout,
        )

    return await anyio.to_thread.run_sync(_call)


async def _ada_call(*, content: str, conversation_id: str | None) -> dict[str, Any]:
    cfg = _agent_config()

    def _call() -> dict[str, Any]:
        return ask_arango_conversation(
            content=content,
            conversation_id=conversation_id,
            config=cfg,
        )

    return await anyio.to_thread.run_sync(_call)


@mcp_genie_code_app.tool(
    name="genie-space-conversation",
    description=(
        "Ask the configured Databricks **Genie Space** (AI/BI) a question in natural language. "
        "Returns Genie's answer and conversation_id for follow-ups. Use for UC/SQL/BI context, "
        "not for raw Arango AQL."
    ),
)
async def genie_space_conversation(
    content: str = Field(..., description="User question for the Genie Space."),
    conversation_id: str | None = Field(
        default=None,
        description="Optional prior conversation_id to continue a thread.",
    ),
) -> str:
    out = await _genie_space_call(content=content.strip(), conversation_id=conversation_id)
    return json.dumps(out, default=str)


@mcp_genie_code_app.tool(
    name="ada-conversation",
    description=(
        "Cluster **ADA** (or forwarded) natural-language conversation when "
        "``ARANGO_CONVERSATION_URL`` is set on the agent app; otherwise returns a stub error. "
        "Use for Arango-cluster ADA, not Genie Space."
    ),
)
async def ada_conversation(
    content: str = Field(..., description="User message for ADA."),
    conversation_id: str | None = Field(default=None, description="Optional thread id."),
) -> str:
    out = await _ada_call(content=content.strip(), conversation_id=conversation_id)
    return json.dumps(out, default=str)


@mcp_genie_code_app.tool(
    name="create-arango-graph",
    description=(
        "**Stub (phase 1):** UC / corpus graph creation will be wired to arango-gateway-app "
        "pipelines. For now returns guidance; use ``/mcp/internal`` Arango tools for direct graph "
        "DDL in ArangoDB."
    ),
)
async def create_arango_graph(
    spec: str = Field(
        default="{}",
        description="JSON string: target scope, tables, options (ignored in stub).",
    ),
) -> str:
    logger.info("create-arango-graph (stub) spec_len=%s", len(spec or ""))
    return json.dumps(
        {
            "ok": False,
            "phase": "stub",
            "detail": (
                "Not implemented yet. Use internal MCP at …/mcp/internal for Arango graph tools "
                "or gateway /api/databricks-graph/* from automation."
            ),
        }
    )


@mcp_genie_code_app.tool(
    name="search-arango-graph",
    description=(
        "**Stub (phase 1):** Search / traverse over Arango-backed graphs. "
        "Will delegate to gateway or internal MCP. Use ``execute-aql-query`` on ``/mcp/internal`` for now."
    ),
)
async def search_arango_graph(
    query: str = Field(..., description="Natural language or AQL hint for the search."),
) -> str:
    logger.info("search-arango-graph (stub) query_len=%s", len(query or ""))
    return json.dumps(
        {
            "ok": False,
            "phase": "stub",
            "detail": "Not implemented yet. Prefer internal MCP tools (AQL, traversal) on /mcp/internal.",
        }
    )


@mcp_genie_code_app.tool(
    name="upsert-arango-graph",
    description=(
        "**Stub (phase 1):** Upsert nodes/edges into an Arango graph. "
        "Will delegate to gateway bulk APIs. Use document/graph tools on ``/mcp/internal`` meanwhile."
    ),
)
async def upsert_arango_graph(
    payload: str = Field(default="{}", description="JSON payload describing upserts (ignored in stub)."),
) -> str:
    logger.info("upsert-arango-graph (stub) payload_len=%s", len(payload or ""))
    return json.dumps(
        {
            "ok": False,
            "phase": "stub",
            "detail": "Not implemented yet. Use internal MCP document/graph tools on /mcp/internal.",
        }
    )
