"""Serving API tool cap for dashboard MCP orchestrator (32 on Databricks Llama)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from arango_agent.services import genie_mcp_orchestrator as orch


def test_tools_for_openai_respects_hard_cap() -> None:
    many = [
        MagicMock(name=f"tool-{i}", description=f"desc {i}", parameters={})
        for i in range(50)
    ]
    for i, m in enumerate(many):
        m.name = f"tool-{i}"

    with patch.object(orch.mcp_app._tool_manager, "list_tools", return_value=many):
        offered = orch._tools_for_openai(max_tools=100, user_text="list collections")

    assert len(offered) == orch.SERVING_API_TOOLS_HARD_CAP


def test_sanitize_additional_properties_for_serving() -> None:
    schema = {
        "type": "object",
        "properties": {
            "bind_vars": {
                "type": "object",
                "additionalProperties": True,
            }
        },
    }
    clean = orch._sanitize_tool_parameters_for_serving(schema)
    assert clean["properties"]["bind_vars"]["additionalProperties"] is False
    assert clean["additionalProperties"] is False


def test_prioritize_puts_list_collections_first() -> None:
    a = MagicMock(name="upsert-document", description="write", parameters={})
    a.name = "upsert-document"
    b = MagicMock(name="list-collections", description="list cols", parameters={})
    b.name = "list-collections"
    c = MagicMock(name="other-tool", description="misc", parameters={})
    c.name = "other-tool"
    picked = orch._prioritize_tools_for_model(
        [a, b, c],
        user_text="list collections in _system",
        cap=2,
    )
    names = [t.name for t in picked]
    assert names[0] == "list-collections"
