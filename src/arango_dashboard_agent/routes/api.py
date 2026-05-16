"""Genie HTTP API (same paths as arango-dashboard-app so the dashboard can reverse-proxy)."""

from __future__ import annotations

import logging
from typing import Any

import requests
from flask import Blueprint, current_app, jsonify, request

from arango_dashboard_agent.services.gateway_url_registry import (
    effective_gateway_base_url,
    invalidate_gateway_url_uc_cache,
)
from arango_dashboard_agent.services.arango_conversation import ask_arango_conversation
from arango_dashboard_agent.services.genie_conversation import ask_genie_conversation
from arango_dashboard_agent.services.databricks_app_http_auth import flask_config_with_inbound_bearer
from arango_dashboard_agent.services.genie_mcp_orchestrator import ask_genie_mcp_conversation_sync
from arango_dashboard_agent.services.genie_registry import (
    invalidate_genie_space_after_acl_error,
    reconcile_genie_uc_registry_for_dashboard_app,
    refresh_genie_space_id_in_app,
)
from arango_dashboard_agent.services.genie_workspace_client import agent_workspace_client
from arango_dashboard_agent.services.startup_debug_genie import run_genie_startup_debug

logger = logging.getLogger(__name__)

api_blueprint = Blueprint("api", __name__)


@api_blueprint.get("/health")
def health():
    return jsonify({"status": "ok"})


@api_blueprint.get("/mcp/diagnostics")
def mcp_diagnostics():
    """Runtime MCP inventory: Genie Code surface (``/mcp``) vs full catalog (``/mcp/internal``) + manifests."""
    try:
        from arango_mcp.genie_code_mcp import mcp_genie_code_app
        from arango_mcp.server import mcp_app as mcp_full_app
        from arango_mcp.tool_registries import genie_code_allowed_tool_names, load_manifest

        def tool_names(app: Any) -> list[str]:
            return [str(t.name) for t in app._tool_manager.list_tools()]

        genie_names = tool_names(mcp_genie_code_app)
        full_names = tool_names(mcp_full_app)
        max_genie_tools = int(current_app.config.get("GENIEMCP_MAX_TOOLS") or 20)
        genie_allowed = genie_code_allowed_tool_names(max_tools=max_genie_tools)
        return jsonify(
            {
                "ok": True,
                "genie_code_mcp": {
                    "http_path": "/mcp",
                    "tool_count": len(genie_names),
                    "tool_names": genie_names,
                    "geniemcp_max_tools": max_genie_tools,
                    "manifest_allowed_tool_names": genie_allowed,
                    "manifest": load_manifest("genie_code"),
                },
                "internal_full_catalog_mcp": {
                    "http_path": "/mcp/internal",
                    "tool_count": len(full_names),
                    "tool_names_preview": full_names[:80],
                    "tool_names_truncated": max(0, len(full_names) - 80),
                    "manifest": load_manifest("full_catalog"),
                },
                "migration_pool_manifest": load_manifest("migration_pool"),
            }
        )
    except Exception as exc:
        logger.warning("mcp_diagnostics failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


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


@api_blueprint.post("/arango/chat")
def arango_chat():
    """
    Direct **ADA** API on the agent (optional). The **dashboard** ADA selector proxies to
    **arango-gateway-app** ``/api/arango/chat`` instead. Genie Code ADA uses gateway via
    ``arango-ada-conversation`` on ``/mcp``.
    """
    payload = request.get_json(silent=True) or {}
    content = str(
        payload.get("content") or payload.get("message") or ""
    ).strip()
    if not content:
        return jsonify({"ok": False, "error": "content or message is required"}), 400

    conversation_id = payload.get("conversation_id")
    if conversation_id is not None:
        conversation_id = str(conversation_id).strip() or None

    result = ask_arango_conversation(
        content=content,
        conversation_id=conversation_id,
        config=current_app.config,
    )
    status = 200 if result.get("ok") else 502
    return jsonify(result), status


@api_blueprint.post("/genie-mcp/chat")
def genie_mcp_chat():
    """
    Dashboard **MCP** mode: workspace foundation-model chat with this app's **full-catalog**
    FastMCP tools (HTTP ``/mcp/internal`` registry). Same JSON body as ``/api/genie/chat``.
    Serving endpoint: ``TOOL_ROUTER_SERVING_ENDPOINT`` if set, else ``GENIEMCP_SERVING_ENDPOINT``.
    """
    payload = request.get_json(silent=True) or {}
    content = str(
        payload.get("content") or payload.get("message") or ""
    ).strip()
    if not content:
        return jsonify({"ok": False, "error": "content or message is required"}), 400

    conversation_id = payload.get("conversation_id")
    if conversation_id is not None:
        conversation_id = str(conversation_id).strip() or None

    try:
        result = ask_genie_mcp_conversation_sync(
            content=content,
            conversation_id=conversation_id,
            config=flask_config_with_inbound_bearer(current_app.config),
        )
    except Exception as exc:
        logger.exception("genie-mcp/chat failed")
        return jsonify({"ok": False, "error": str(exc)}), 500
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
