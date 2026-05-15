"""Unit tests for dashboard MCP mode (``genie_mcp_orchestrator``) — LLM + in-process tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arango_agent.services import genie_mcp_orchestrator as orch


@pytest.fixture
def mcp_config() -> dict[str, Any]:
    return {
        "GENIEMCP_SERVING_ENDPOINT": "databricks-meta-llama-3-3-70b-instruct",
        "GENIEMCP_MAX_TOOLS": 5,
        "GENIEMCP_MAX_ROUNDS": 8,
    }


@pytest.mark.asyncio
async def test_ask_genie_mcp_missing_endpoint() -> None:
    out = await orch.ask_genie_mcp_conversation(
        content="hello",
        conversation_id=None,
        config={},
    )
    assert out["ok"] is False
    assert "No serving endpoint" in out["error"]


def _choice_with_message(*, content: str = "", tool_calls: Any = None) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    return choice


@pytest.mark.asyncio
async def test_ask_genie_mcp_text_reply_no_tools(mcp_config: dict[str, Any]) -> None:
    mock_resp = MagicMock()
    mock_resp.choices = [_choice_with_message(content="Done.", tool_calls=None)]
    mock_resp.id = "resp-1"

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp

    with (
        patch.object(orch, "arango_connector") as mock_conn,
        patch.object(orch, "_workspace_openai_client", return_value=mock_client),
        patch.object(orch, "_tools_for_openai", return_value=[{"type": "function", "function": {"name": "x"}}]),
    ):
        mock_conn.connect = AsyncMock()
        out = await orch.ask_genie_mcp_conversation(
            content="list collections",
            conversation_id=None,
            config=mcp_config,
        )

    assert out["ok"] is True
    assert out["message"]["content"] == "Done."
    mock_client.chat.completions.create.assert_called_once()
    call_kw = mock_client.chat.completions.create.call_args.kwargs
    assert call_kw["model"] == "databricks-meta-llama-3-3-70b-instruct"


@pytest.mark.asyncio
async def test_ask_genie_mcp_invokes_internal_tool(mcp_config: dict[str, Any]) -> None:
    tc = MagicMock()
    tc.id = "call-1"
    tc.function.name = "list-databases"
    tc.function.arguments = "{}"

    mock_resp_tool = MagicMock()
    mock_resp_tool.choices = [_choice_with_message(content="", tool_calls=[tc])]
    mock_resp_tool.id = "r1"

    mock_resp_final = MagicMock()
    mock_resp_final.choices = [_choice_with_message(content="Found databases.", tool_calls=None)]
    mock_resp_final.id = "r2"

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [mock_resp_tool, mock_resp_final]

    with (
        patch.object(orch, "arango_connector") as mock_conn,
        patch.object(orch, "_workspace_openai_client", return_value=mock_client),
        patch.object(
            orch,
            "_tools_for_openai",
            return_value=[
                {
                    "type": "function",
                    "function": {
                        "name": "list-databases",
                        "description": "list dbs",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        ),
        patch.object(orch.mcp_app._tool_manager, "call_tool", new_callable=AsyncMock) as mock_call,
    ):
        mock_conn.connect = AsyncMock()
        mock_call.return_value = '["_system"]'

        out = await orch.ask_genie_mcp_conversation(
            content="what databases exist?",
            conversation_id=None,
            config=mcp_config,
        )

    assert out["ok"] is True
    assert "Found databases" in out["message"]["content"]
    mock_call.assert_awaited_once()
    assert mock_client.chat.completions.create.call_count == 2
