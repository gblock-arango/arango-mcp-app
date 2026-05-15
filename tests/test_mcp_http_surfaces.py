"""HTTP MCP surfaces: Genie Code ``/mcp`` and full catalog ``/mcp/internal``.

Uses the official MCP Python client against an in-process ASGI app (no deployed Databricks App).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.routing import Mount

from arango_mcp.genie_code_mcp import mcp_genie_code_app
from arango_mcp.server import mcp_app

GENIE_TOOL_NAMES = {
    "arango-graph-machine-learning",
    "arango-ada-conversation",
    "arango-graph-queries",
}


@pytest.fixture
def _patch_arango_connect():
    with (
        patch(
            "arango_mcp.arango_connector.arango_connector.connect",
            new_callable=AsyncMock,
        ),
        patch(
            "arango_mcp.arango_connector.arango_connector.disconnect",
            new_callable=AsyncMock,
        ),
    ):
        yield


@asynccontextmanager
async def _genie_mcp_client(
    _patch_arango_connect,
) -> AsyncIterator[tuple[str, httpx.AsyncClient]]:
    inner = mcp_genie_code_app.streamable_http_app()
    starlette_app = Starlette(routes=[Mount("/mcp", app=inner)])
    transport = httpx.ASGITransport(app=starlette_app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    async with (
        mcp_genie_code_app.session_manager.run(),
        starlette_app.router.lifespan_context(starlette_app),
        http_client,
    ):
        yield "http://testserver/mcp/", http_client


@asynccontextmanager
async def _internal_mcp_client(
    _patch_arango_connect,
) -> AsyncIterator[tuple[str, httpx.AsyncClient]]:
    inner = mcp_app.streamable_http_app()
    starlette_app = Starlette(routes=[Mount("/mcp/internal", app=inner)])
    transport = httpx.ASGITransport(app=starlette_app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    async with (
        mcp_app.session_manager.run(),
        starlette_app.router.lifespan_context(starlette_app),
        http_client,
    ):
        yield "http://testserver/mcp/internal/", http_client


@pytest.mark.mcp_http
async def test_genie_mcp_initialize_and_list_tools(_patch_arango_connect) -> None:
    """MCP protocol handshake against the Genie Code surface (what Genie Code runs on Save)."""
    async with _genie_mcp_client(_patch_arango_connect) as (url, http_client):
        async with (
            streamable_http_client(url, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
    names = {t.name for t in listed.tools}
    assert names == GENIE_TOOL_NAMES
    assert len(names) == 3
    assert len(names) <= 20


@pytest.mark.mcp_http
async def test_internal_mcp_initialize_and_list_tools(_patch_arango_connect) -> None:
    """Full catalog MCP responds to initialize + tools/list (dashboard / ToolRouter path)."""
    async with _internal_mcp_client(_patch_arango_connect) as (url, http_client):
        async with (
            streamable_http_client(url, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
    assert len(listed.tools) >= 10
    assert any(t.name == "list-collections" for t in listed.tools)


@pytest.mark.mcp_http
def test_genie_code_tool_manager_matches_expected_names() -> None:
    """Fast registration sanity check without HTTP."""
    names = {t.name for t in mcp_genie_code_app._tool_manager.list_tools()}
    assert names == GENIE_TOOL_NAMES
