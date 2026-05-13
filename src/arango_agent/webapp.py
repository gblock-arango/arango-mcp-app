"""Flask application factory for the arango-agent Databricks App (HTTP + Genie)."""

from __future__ import annotations

import logging

from flask import Flask, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix

from arango_mcp.config import flask_app_config, settings
from arango_agent.routes.api import api_blueprint
from arango_agent.services.agent_url_registry import publish_self_agent_url_to_uc_if_configured
from arango_agent.services.genie_registry import bootstrap_genie_space_id_from_uc
from arango_agent.services.startup_debug_genie import run_genie_startup_debug

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(flask_app_config(settings))

    publish_self_agent_url_to_uc_if_configured(app)

    try:
        bootstrap_genie_space_id_from_uc(app)
    except Exception:
        logger.exception(
            "Genie UC bootstrap failed at startup; app will run but Genie may be unavailable "
            "until /api/genie/chat refresh succeeds."
        )

    @app.get("/health")
    def health_root():
        return jsonify({"status": "ok"})

    app.register_blueprint(api_blueprint, url_prefix="/api")

    app.extensions["startup_debug_status"] = {
        "status": "not_run",
        "message": "Set DEBUG_STARTUP_CHECKS=true to run startup diagnostics.",
    }
    if app.config.get("DEBUG_STARTUP_CHECKS", False):
        app.extensions["startup_debug_status"] = run_genie_startup_debug(app)

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
        x_prefix=1,
    )
    return app
