"""Three Genie Code MCP tools at ``/mcp`` (≤ Databricks 20-tool workspace cap).

- ``arango-graph-machine-learning`` — GraphML stub (future Gold-table graphlets → job).
- ``arango-ada-conversation`` — AMP ADA via **arango-gateway-app** ``/api/arango/chat``.
- ``arango-graph-queries`` — workspace LLM + full internal MCP catalog (``genie_mcp_orchestrator``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anyio
from pydantic import Field

from arango_agent.services.databricks_app_http_auth import config_with_inbound_bearer
from arango_agent.services.gateway_ada_conversation import ask_gateway_ada_conversation
from arango_agent.services.genie_mcp_orchestrator import ask_genie_mcp_conversation
from arango_mcp.config import flask_app_config, settings
from arango_mcp.genie_code_mcp import mcp_genie_code_app

logger = logging.getLogger(__name__)


def _agent_config() -> dict[str, Any]:
    """Flask/MCP inbound bearer → ``OUTBOUND_BEARER_TOKEN`` for gateway app→app calls."""
    return config_with_inbound_bearer(flask_app_config(settings))


def _json(out: dict[str, Any]) -> str:
    return json.dumps(out, default=str)


async def _gateway_ada_call(*, content: str, conversation_id: str | None) -> dict[str, Any]:
    cfg = _agent_config()

    def _call() -> dict[str, Any]:
        return ask_gateway_ada_conversation(
            content=content,
            conversation_id=conversation_id,
            config=cfg,
        )

    return await anyio.to_thread.run_sync(_call)


@mcp_genie_code_app.tool(
    name="arango-graph-machine-learning",
    description=(
        "Run (future) **GraphML** training or inference on graphlets from one or more **Gold** UC table "
        "rows. **Stub today** — records inputs until the gateway GraphML job is wired. "
        "Not for ad-hoc AQL; use ``arango-graph-queries``."
    ),
)
async def arango_graph_machine_learning(
    gold_table_rows: str = Field(
        ...,
        description=(
            "JSON array of Gold table row references (graphlets), e.g. "
            '[{"catalog":"c","schema":"s","table":"t","row_id":"…"}].'
        ),
    ),
    options: str = Field(
        default="{}",
        description="Optional JSON object: model, hyperparameters, output volume path, etc.",
    ),
) -> str:
    logger.info(
        "arango-graph-machine-learning (stub) rows_len=%s options_len=%s",
        len(gold_table_rows or ""),
        len(options or ""),
    )
    return _json(
        {
            "ok": False,
            "phase": "stub",
            "detail": (
                "GraphML job not implemented yet. Will consume Gold-table graphlet rows and run an "
                "AMP GraphML pipeline via arango-gateway-app when available."
            ),
            "docs": "https://docs.arango.ai/amp/",
            "gold_table_rows_received": (gold_table_rows or "")[:4000],
            "options_received": (options or "")[:2000],
        }
    )


@mcp_genie_code_app.tool(
    name="arango-ada-conversation",
    description=(
        "Natural-language chat with **Arango Managed Platform ADA** (AMP). Calls "
        "**arango-gateway-app** ``POST /api/arango/chat``, which forwards to the gateway's "
        "``ARANGO_CONVERSATION_URL`` (AMP ADA endpoint). Returns ``conversation_id`` for follow-ups."
    ),
)
async def arango_ada_conversation(
    content: str = Field(..., description="User message for AMP ADA."),
    conversation_id: str | None = Field(
        default=None,
        description="Optional conversation_id from a prior ADA reply.",
    ),
) -> str:
    out = await _gateway_ada_call(content=content.strip(), conversation_id=conversation_id)
    return _json(out)


@mcp_genie_code_app.tool(
    name="arango-graph-queries",
    description=(
        "Arango graph operations via the workspace **LLM** and the full **internal MCP tool catalog** "
        "(AQL, collections, graphs, traversal, documents, etc.). Same orchestrator as dashboard "
        "``POST /api/genie-mcp/chat`` — use this for search, create, upsert, and exploratory queries."
    ),
)
async def arango_graph_queries(
    query: str = Field(
        ...,
        description="Natural-language request for Arango (search, DDL, upsert, diagnostics, …).",
    ),
    conversation_id: str | None = Field(
        default=None,
        description="Optional orchestrator conversation_id for multi-step follow-ups.",
    ),
) -> str:
    cfg = _agent_config()
    logger.info("arango-graph-queries → genie_mcp_orchestrator (conversation_id=%s)", conversation_id)
    out = await ask_genie_mcp_conversation(
        content=query.strip(),
        conversation_id=conversation_id,
        config=cfg,
    )
    return _json(out)
