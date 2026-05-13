"""Gunicorn entrypoint (lives under ``src/`` so ``PYTHONPATH=src`` does not depend on cwd).

Stdio MCP uses root ``app.py`` → :mod:`arango_mcp.main`. When HTTP/streamable MCP is added,
replace ``app`` with an ASGI/WSGI application like ``arango-gateway-app`` does.
"""

app = None  # type: ignore[assignment]
