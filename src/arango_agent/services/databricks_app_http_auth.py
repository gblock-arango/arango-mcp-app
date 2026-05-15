"""Bearer auth for HTTPS calls from this app to other ``*.databricksapps.com`` apps."""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Any, Mapping

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_mcp_inbound_bearer: ContextVar[str] = ContextVar("mcp_inbound_bearer", default="")


def bearer_token_from_headers(headers: Any) -> str:
    """Extract user bearer from ``Authorization`` or ``x-forwarded-access-token``."""
    forwarded = ""
    authorization = ""
    for key, value in headers.items():
        kl = str(key).lower()
        if kl == "x-forwarded-access-token" and (value or "").strip():
            forwarded = str(value).strip()
        elif kl == "authorization":
            authorization = str(value or "").strip()
    if forwarded:
        return forwarded
    if authorization.lower().startswith("bearer ") and len(authorization) > 7:
        return authorization[7:].strip()
    return ""


def inbound_bearer_token_from_flask() -> str:
    """User/dashboard token on the current Flask request (empty if absent)."""
    try:
        from flask import has_request_context, request
    except ImportError:
        return ""
    if not has_request_context():
        return ""
    return bearer_token_from_headers(request.headers)


def inbound_bearer_token_from_mcp() -> str:
    """User token on the current MCP Streamable HTTP request (Genie Code / ToolRouter)."""
    token = (_mcp_inbound_bearer.get() or "").strip()
    if token:
        return token
    try:
        from mcp.server.lowlevel.server import request_ctx

        ctx = request_ctx.get()
        req = getattr(ctx, "request", None)
        if req is not None and hasattr(req, "headers"):
            return bearer_token_from_headers(req.headers)
    except LookupError:
        pass
    return ""


def inbound_bearer_token() -> str:
    """Flask dashboard proxy token, else MCP/Genie Code request token."""
    for source in (inbound_bearer_token_from_flask, inbound_bearer_token_from_mcp):
        token = source()
        if token:
            return token
    return ""


def config_with_inbound_bearer(base_config: Mapping[str, Any]) -> dict[str, Any]:
    """Copy config and attach ``OUTBOUND_BEARER_TOKEN`` when the inbound request carries one."""
    cfg = dict(base_config)
    token = inbound_bearer_token()
    if token:
        cfg["OUTBOUND_BEARER_TOKEN"] = token
    return cfg


class McpInboundBearerMiddleware(BaseHTTPMiddleware):
    """Capture Genie Code / MCP client ``Authorization`` for gateway app→app calls."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        token = bearer_token_from_headers(request.headers)
        reset: Token[str] | None = None
        if token:
            reset = _mcp_inbound_bearer.set(token)
        try:
            return await call_next(request)
        finally:
            if reset is not None:
                _mcp_inbound_bearer.reset(reset)


def outbound_bearer_authorization_header(
    *,
    config: Mapping[str, Any] | None = None,
    override_token: str | None = None,
) -> dict[str, str]:
    """
    ``Authorization`` header for app→app HTTP to peer Databricks Apps.

    Precedence: ``override_token`` → ``config['OUTBOUND_BEARER_TOKEN']`` (inbound Flask/MCP) →
    ``ARANGO_GATEWAY_BEARER_TOKEN`` env → app ``WorkspaceClient`` OAuth (needs CAN_USE on target app).
    """
    import os

    for candidate in (
        (override_token or "").strip(),
        str((config or {}).get("OUTBOUND_BEARER_TOKEN") or "").strip(),
        (os.environ.get("ARANGO_GATEWAY_BEARER_TOKEN") or "").strip(),
    ):
        if candidate:
            return {"Authorization": f"Bearer {candidate}"}

    try:
        from arango_agent.services.genie_workspace_client import agent_workspace_client

        auth = agent_workspace_client().config.authenticate() or {}
        lower = {str(k).lower(): v for k, v in auth.items()}
        bearer = str(lower.get("authorization") or "").strip()
        if bearer:
            return {"Authorization": bearer}
        token = str(lower.get("token") or "").strip()
        if token:
            return {"Authorization": f"Bearer {token}"}
    except Exception as exc:
        logger.warning("Could not obtain workspace bearer for outbound Apps HTTP: %s", exc)
    return {}


def flask_config_with_inbound_bearer(base_config: Mapping[str, Any]) -> dict[str, Any]:
    """Copy Flask config and attach ``OUTBOUND_BEARER_TOKEN`` when the request carries one."""
    return config_with_inbound_bearer(base_config)
