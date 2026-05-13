"""Local MCP entrypoint (stdio). Databricks Apps HTTP uses ``gunicorn wsgi:app`` — see ``app.yaml``."""

from arango_mcp.main import run_server_cli

if __name__ == "__main__":
    run_server_cli()
