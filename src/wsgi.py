"""Gunicorn entrypoint (``PYTHONPATH=src`` → ``wsgi:app``)."""

from arango_agent.webapp import create_app

app = create_app()
