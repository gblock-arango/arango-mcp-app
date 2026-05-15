"""Genie Code tools: orchestrator and gateway ADA delegation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from arango_mcp.mcp_tools import genie_code_tools as gc


@pytest.mark.asyncio
async def test_arango_graph_queries_delegates_to_orchestrator() -> None:
    expected = {
        "ok": True,
        "tools_invoked": ["list-databases"],
        "message": {"content": "Found _system.", "status": "SUCCEEDED"},
    }
    with patch.object(gc, "ask_genie_mcp_conversation", new_callable=AsyncMock) as mock_orch:
        mock_orch.return_value = expected
        raw = await gc.arango_graph_queries(query="list databases", conversation_id="cid-1")

    mock_orch.assert_awaited_once_with(
        content="list databases",
        conversation_id="cid-1",
        config=mock_orch.await_args.kwargs["config"],
    )
    assert json.loads(raw) == expected


@pytest.mark.asyncio
async def test_arango_ada_conversation_calls_gateway() -> None:
    expected = {"ok": True, "conversation_id": "ada-1", "message": {"content": "hi"}}
    with patch(
        "arango_mcp.mcp_tools.genie_code_tools.ask_gateway_ada_conversation",
        return_value=expected,
    ) as mock_gw:
        with patch(
            "arango_mcp.mcp_tools.genie_code_tools.anyio.to_thread.run_sync",
            new=AsyncMock(side_effect=lambda fn, *a, **k: fn()),
        ):
            raw = await gc.arango_ada_conversation(content="hello", conversation_id=None)

    mock_gw.assert_called_once()
    assert json.loads(raw) == expected


@pytest.mark.asyncio
async def test_arango_graph_machine_learning_stub() -> None:
    raw = await gc.arango_graph_machine_learning(
        gold_table_rows='[{"table":"gold.graphlets"}]',
        options="{}",
    )
    data = json.loads(raw)
    assert data["ok"] is False
    assert data["phase"] == "stub"
