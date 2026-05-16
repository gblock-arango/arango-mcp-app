"""Genie Code /mcp tool cap (manifest + GENIEMCP_MAX_TOOLS), separate from inner orchestrator."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from arango_mcp.tool_registries import genie_code_allowed_tool_names


def test_genie_code_allowed_tool_names_respects_cap() -> None:
    all_names = genie_code_allowed_tool_names(max_tools=100)
    capped = genie_code_allowed_tool_names(max_tools=2)
    assert len(capped) == 2
    assert capped == all_names[:2]


def test_tools_for_openai_uses_full_catalog_without_slice() -> None:
    from arango_dashboard_agent.services import genie_mcp_orchestrator as orch

    tools = [
        SimpleNamespace(name=f"tool-{i}", description=f"d{i}", parameters={})
        for i in range(12)
    ]
    with patch.object(orch.mcp_app._tool_manager, "list_tools", return_value=tools):
        out = orch._tools_for_openai()
    assert len(out) == 12
    assert out[0]["function"]["name"] == "tool-0"
    assert out[11]["function"]["name"] == "tool-11"
