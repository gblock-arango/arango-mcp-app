"""ASGI entrypoint: stateless MCP at ``/mcp`` (Genie Code) + Flask (Genie HTTP, ``/api``).

Run with Gunicorn’s Uvicorn worker (see ``app.yaml``) so Starlette lifespan runs and the
Streamable HTTP session manager stays active for MCP requests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.routing import Mount
from starlette.types import ASGIApp

from arango_agent.webapp import create_app
from arango_mcp.config import settings
from arango_mcp.server import mcp_app


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
        Mount("/mcp", app=_mcp_asgi),
        Mount("/", app=WSGIMiddleware(_flask)),
    ],
    lifespan=_lifespan,
)
