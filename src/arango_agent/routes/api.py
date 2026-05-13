"""Genie HTTP API (same paths as arango-dashboard-app so the dashboard can reverse-proxy)."""

from __future__ import annotations

import logging

import requests
from flask import Blueprint, current_app, jsonify, request

from arango_agent.services.gateway_url_registry import (
    effective_gateway_base_url,
    invalidate_gateway_url_uc_cache,
)
from arango_agent.services.genie_conversation import ask_genie_conversation
from arango_agent.services.genie_registry import (
    invalidate_genie_space_after_acl_error,
    reconcile_genie_uc_registry_for_dashboard_app,
    refresh_genie_space_id_in_app,
)
from arango_agent.services.genie_workspace_client import agent_workspace_client
from arango_agent.services.startup_debug_genie import run_genie_startup_debug

logger = logging.getLogger(__name__)

api_blueprint = Blueprint("api", __name__)


@api_blueprint.get("/health")
def health():
    return jsonify({"status": "ok"})


@api_blueprint.post("/genie/chat")
def genie_chat():
    refresh_genie_space_id_in_app(current_app)
    space_id = str(current_app.config.get("GENIE_SPACE_ID") or "").strip()
    if not space_id:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Genie space id is not set: UC registry has no active row and auto-provision "
                    "did not succeed. Check Databricks app logs for 'Genie auto-provision failed', "
                    "and GET /api/debug/startup-status when DEBUG_STARTUP_CHECKS=true."
                ),
            }
        ), 503

    payload = request.get_json(silent=True) or {}
    content = str(
        payload.get("content") or payload.get("message") or ""
    ).strip()
    if not content:
        return jsonify({"ok": False, "error": "content or message is required"}), 400

    conversation_id = payload.get("conversation_id")
    if conversation_id is not None:
        conversation_id = str(conversation_id).strip() or None

    timeout = float(current_app.config.get("GENIE_MESSAGE_TIMEOUT_SECONDS") or 600.0)

    result = ask_genie_conversation(
        workspace_client=agent_workspace_client(),
        space_id=space_id,
        content=content,
        conversation_id=conversation_id,
        timeout_seconds=timeout,
    )
    if not result.get("ok"):
        err = str(result.get("error") or "")
        if invalidate_genie_space_after_acl_error(
            current_app, space_id=space_id, error_message=err
        ):
            refresh_genie_space_id_in_app(current_app)
            new_sid = str(current_app.config.get("GENIE_SPACE_ID") or "").strip()
            if new_sid and new_sid != space_id:
                result = ask_genie_conversation(
                    workspace_client=agent_workspace_client(),
                    space_id=new_sid,
                    content=content,
                    conversation_id=None,
                    timeout_seconds=timeout,
                )
    status = 200 if result.get("ok") else 502
    return jsonify(result), status


@api_blueprint.post("/deploy/reconcile-genie")
def deploy_reconcile_genie():
    """
    Post-deploy hook: validate UC Genie registry vs ``get_space`` for **this app's** identity,
    repair the active UC row, or create a new Genie space. Called by ``deploy_app.sh`` after
    ``databricks apps deploy`` (Bearer token: PAT or ``databricks auth token`` U2M cache).
    """
    payload = reconcile_genie_uc_registry_for_dashboard_app(current_app)
    status = 200 if payload.get("ok") else 500
    return jsonify(payload), status


@api_blueprint.get("/debug/startup-status")
def startup_status():
    """Merge gateway Arango/UC probe (optional) with Genie diagnostics for this app."""
    refresh = str(request.args.get("refresh", "false")).lower() == "true"
    if refresh:
        invalidate_gateway_url_uc_cache()
    base = effective_gateway_base_url(current_app.config)

    gw_payload: dict = {}
    if base:
        try:
            params = {"refresh": "true"} if refresh else {}
            r = requests.get(
                f"{base}/api/debug/startup-status",
                params=params,
                timeout=20.0,
            )
            if r.ok:
                gw_payload = r.json() if r.content else {}
            else:
                gw_payload = {
                    "gateway_http_status": r.status_code,
                    "gateway_body_preview": (r.text or "")[:800],
                }
        except Exception as exc:
            logger.warning("Gateway startup-status fetch failed: %s", exc)
            gw_payload = {"gateway_unreachable": str(exc)}
    else:
        gw_payload = {
            "gateway": "not_configured",
            "hint": (
                "Set ARANGO_GATEWAY_BASE_URL or ensure arango-gateway-app has published to "
                "ARANGO_GATEWAY_REGISTRY_TABLE."
            ),
        }

    genie_status = run_genie_startup_debug(current_app, retry_genie=refresh)
    merged = {
        **gw_payload,
        "genie": genie_status.get("genie"),
        "gateway_base_url_effective": base or None,
    }
    return jsonify(merged)
