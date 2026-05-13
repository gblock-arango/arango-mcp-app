"""ASGI entrypoint: stateless MCP at ``/mcp`` (Genie Code) + Flask (Genie HTTP, ``/api``).

Run with Gunicorn’s Uvicorn worker (see ``app.yaml``) so Starlette lifespan runs and the
Streamable HTTP session manager stays active for MCP requests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount
from starlette.types import ASGIApp

from arango_agent.webapp import create_app
from arango_mcp.config import settings
from arango_mcp.server import mcp_app


class _BrowserFriendlyMcpGet(BaseHTTPMiddleware):
    """Plain browser GETs use ``Accept: text/html``; MCP GET requires ``text/event-stream``.

    Avoid returning JSON-RPC ``Not Acceptable`` when someone pastes ``/mcp`` into a tab to smoke-test.
    Genie Code and real MCP clients send the right ``Accept`` value and are unchanged.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method != "GET":
            return await call_next(request)
        path = request.url.path
        if path not in ("/mcp", "/mcp/"):
            return await call_next(request)
        accept = (request.headers.get("accept") or "").lower()
        if "text/event-stream" in accept:
            return await call_next(request)
        return JSONResponse(
            {
                "ok": True,
                "endpoint": "mcp-streamable-http",
                "message": (
                    "MCP is up. Browsers do not send the MCP protocol headers on a normal GET. "
                    "Genie Code and MCP clients use Accept: text/event-stream for this GET (and "
                    "application/json for JSON-RPC POST). This response is only for human/browser checks."
                ),
            }
        )


class _NormalizeMcpPath(BaseHTTPMiddleware):
    """Starlette ``Mount("/mcp", …)`` compiles to ``^/mcp/(?P<path>.*)$``, so ``/mcp`` alone does not match.

    Genie Code expects ``https://…/mcp`` (no trailing slash). Rewrite ``/mcp`` → ``/mcp/`` before routing
    so the mount and inner ``streamable_http_path="/"`` line up.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.scope["type"] == "http" and request.scope.get("path") == "/mcp":
            request.scope["path"] = "/mcp/"
            request.scope["raw_path"] = b"/mcp/"
        return await call_next(request)


def _parse_cors_origins(raw: str) -> list[str] | None:
    s = (raw or "").strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return parts or None


def _build_mcp_asgi() -> ASGIApp:
    """Streamable HTTP MCP; optional CORS for browser / Genie Code."""
    inner = mcp_app.streamable_http_app()
    origins = _parse_cors_origins(settings.mcp_cors_allow_origins)
    if not origins:
        return inner
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


@asynccontextmanager
async def _lifespan(_: Starlette) -> AsyncIterator[None]:
    # Mounted apps do not receive ASGI lifespan; run the MCP session manager at the root.
    async with mcp_app.session_manager.run():
        yield


_flask = create_app()
_mcp_asgi = _build_mcp_asgi()

app = Starlette(
    routes=[
        Mount("/mcp/", app=_mcp_asgi),
        Mount("/", app=WSGIMiddleware(_flask)),
    ],
    lifespan=_lifespan,
    middleware=[
        Middleware(_NormalizeMcpPath),
        Middleware(_BrowserFriendlyMcpGet),
    ],
)
