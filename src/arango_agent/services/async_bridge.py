"""Run async orchestration on the Starlette/Uvicorn main loop from sync Flask workers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _main_loop
    _main_loop = loop


def run_on_main_loop(coro: Coroutine[Any, Any, T], *, timeout: float = 660.0) -> T:
    """
    Schedule ``coro`` on the ASGI lifespan loop (where MCP ``session_manager`` runs).

    Flask/gunicorn sync workers must not use ``anyio.run()`` for MCP tool calls — that creates
    a different loop than ``StreamableHTTPSessionManager`` and can hang or yield empty responses.
    """
    loop = _main_loop
    if loop is None or not loop.is_running():
        logger.warning("Main event loop not set; falling back to asyncio.run() for MCP orchestration")
        return asyncio.run(coro)

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except Exception as exc:
        future.cancel()
        raise exc
