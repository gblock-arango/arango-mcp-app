"""Inbound bearer extraction for Flask, MCP (Genie Code), and outbound gateway headers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from arango_dashboard_agent.services import databricks_app_http_auth as auth


def test_bearer_token_from_authorization_header() -> None:
    headers = {"Authorization": "Bearer user-token-abc"}
    assert auth.bearer_token_from_headers(headers) == "user-token-abc"


def test_bearer_token_from_forwarded_access_token() -> None:
    headers = {"x-forwarded-access-token": "fwd-token"}
    assert auth.bearer_token_from_headers(headers) == "fwd-token"


def test_config_with_inbound_bearer_from_mcp_contextvar() -> None:
    token = auth._mcp_inbound_bearer.set("mcp-user-token")
    try:
        cfg = auth.config_with_inbound_bearer({"ARANGO_GATEWAY_BASE_URL": "https://gw"})
        assert cfg["OUTBOUND_BEARER_TOKEN"] == "mcp-user-token"
    finally:
        auth._mcp_inbound_bearer.reset(token)


def test_mcp_middleware_sets_contextvar() -> None:
    captured: list[str] = []

    async def handler(request: Request) -> PlainTextResponse:
        captured.append(auth.inbound_bearer_token_from_mcp())
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[Route("/mcp/", handler)],
        middleware=[Middleware(auth.McpInboundBearerMiddleware)],
    )
    client = TestClient(app)
    resp = client.get("/mcp/", headers={"Authorization": "Bearer genie-token"})
    assert resp.status_code == 200
    assert captured == ["genie-token"]
    assert auth.inbound_bearer_token_from_mcp() == ""


def test_outbound_header_prefers_config_bearer() -> None:
    hdr = auth.outbound_bearer_authorization_header(
        config={"OUTBOUND_BEARER_TOKEN": "from-config"},
    )
    assert hdr == {"Authorization": "Bearer from-config"}


def test_inbound_bearer_from_mcp_request_ctx() -> None:
    try:
        from mcp.server.lowlevel.server import request_ctx
        from mcp.server.lowlevel.server import RequestContext
    except ImportError:
        pytest.skip("mcp package not installed")

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": "Bearer ctx-token"}
    ctx = MagicMock(spec=RequestContext)
    ctx.request = mock_request
    token = request_ctx.set(ctx)
    try:
        assert auth.inbound_bearer_token_from_mcp() == "ctx-token"
    finally:
        request_ctx.reset(token)
