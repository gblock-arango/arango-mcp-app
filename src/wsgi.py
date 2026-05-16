"""Gunicorn WSGI entrypoint: Flask only (no HTTP ``/mcp``).

The Databricks App uses ``asgi:app`` with a Uvicorn worker — see ``asgi.py`` and ``app.yaml``.
"""

from arango_dashboard_agent.webapp import create_app

app = create_app()
