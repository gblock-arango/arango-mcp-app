"""ASGI entrypoint: Genie Code MCP at ``/mcp`` + full catalog at ``/mcp/internal`` + Flask (``/api``).

Run with Gunicorn’s Uvicorn worker (see ``app.yaml``) so Starlette lifespan runs and both MCP
session managers stay active.

When ``MCP_CORS_ALLOW_ORIGINS`` is unset, MCP routes enable CORS for the workspace origin parsed
from ``DATABRICKS_HOST`` (injected in Databricks Apps) so Genie Code can call ``/mcp`` without
post-deploy CORS edits.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlparse

from arango_dashboard_agent.services.async_bridge import set_main_event_loop

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount
from starlette.types import ASGIApp

from arango_dashboard_agent.services.databricks_app_http_auth import McpInboundBearerMiddleware
from arango_dashboard_agent.webapp import create_app
from arango_mcp.config import settings
from arango_mcp.genie_code_mcp import mcp_genie_code_app
from arango_mcp.server import mcp_app

logger = logging.getLogger(__name__)


class _BrowserFriendlyMcpGet(BaseHTTPMiddleware):
    """Plain browser GETs use ``Accept: text/html``; MCP GET requires ``text/event-stream``."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method != "GET":
            return await call_next(request)
        path = request.url.path
        if path not in ("/mcp", "/mcp/", "/mcp/internal", "/mcp/internal/"):
            return await call_next(request)
        accept = (request.headers.get("accept") or "").lower()
        if "text/event-stream" in accept:
            return await call_next(request)
        return JSONResponse(
            {
                "ok": True,
                "endpoint": "mcp-streamable-http",
                "path": path,
                "message": (
                    "MCP is up. Browsers do not send the MCP protocol headers on a normal GET. "
                    "Genie Code and MCP clients use Accept: text/event-stream for this GET (and "
                    "application/json for JSON-RPC POST). This response is only for human/browser checks."
                ),
            }
        )


class _NormalizeMcpPaths(BaseHTTPMiddleware):
    """Rewrite bare ``/mcp`` and ``/mcp/internal`` so Starlette ``Mount(.../)`` matches."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.scope["type"] == "http":
            p = request.scope.get("path")
            if p == "/mcp":
                request.scope["path"] = "/mcp/"
                request.scope["raw_path"] = b"/mcp/"
            elif p == "/mcp/internal":
                request.scope["path"] = "/mcp/internal/"
                request.scope["raw_path"] = b"/mcp/internal/"
        return await call_next(request)


def _parse_cors_origins(raw: str) -> list[str] | None:
    s = (raw or "").strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return parts or None


def _workspace_origin_from_databricks_host() -> str | None:
    """Workspace web UI origin for Genie Code cross-origin calls to this App (scheme + host, no path)."""
    raw = (os.environ.get("DATABRICKS_HOST") or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/")
    u = urlparse(raw)
    if not u.scheme or not u.netloc:
        return None
    return f"{u.scheme}://{u.netloc}"


def _mcp_cors_allowlist_raw() -> str:
    """Explicit ``MCP_CORS_ALLOW_ORIGINS`` wins; else use ``DATABRICKS_HOST`` origin in App runtime."""
    explicit = (settings.mcp_cors_allow_origins or "").strip()
    if explicit:
        return explicit
    return (_workspace_origin_from_databricks_host() or "").strip()


def _wrap_mcp_cors(inner: ASGIApp) -> ASGIApp:
    raw = _mcp_cors_allowlist_raw()
    origins = _parse_cors_origins(raw)
    if not origins:
        return inner
    auto = not (settings.mcp_cors_allow_origins or "").strip()
    if auto:
        logger.info("MCP CORS: MCP_CORS_ALLOW_ORIGINS unset — using %s (from DATABRICKS_HOST)", origins)
    if origins == ["*"]:
        return CORSMiddleware(
            inner,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            allow_credentials=False,
        )
    return CORSMiddleware(
        inner,
        allow_origins=origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=True,
    )


_genie_inner = mcp_genie_code_app.streamable_http_app()
_full_inner = mcp_app.streamable_http_app()
_mcp_genie_asgi = _wrap_mcp_cors(McpInboundBearerMiddleware(_genie_inner))
_mcp_full_asgi = _wrap_mcp_cors(McpInboundBearerMiddleware(_full_inner))


@asynccontextmanager
async def _lifespan(_: Starlette) -> AsyncIterator[None]:
    set_main_event_loop(asyncio.get_running_loop())
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_genie_code_app.session_manager.run())
        await stack.enter_async_context(mcp_app.session_manager.run())
        yield
    set_main_event_loop(None)


_flask = create_app()

app = Starlette(
    routes=[
        Mount("/mcp/internal/", app=_mcp_full_asgi),
        Mount("/mcp/", app=_mcp_genie_asgi),
        Mount("/", app=WSGIMiddleware(_flask)),
    ],
    lifespan=_lifespan,
    middleware=[
        Middleware(_NormalizeMcpPaths),
        Middleware(_BrowserFriendlyMcpGet),
    ],
)
