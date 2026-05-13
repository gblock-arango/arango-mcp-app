"""WSGI-style root module mirroring ``arango-gateway-app/app.py`` layout.

Databricks Apps: see ``app.yaml`` (``PYTHONPATH=src`` + ``python app.py``). Local: ``poetry run python app.py``.
"""

from arango_mcp.main import run_server_cli

if __name__ == "__main__":
    run_server_cli()
