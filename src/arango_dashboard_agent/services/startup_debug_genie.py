"""Genie-only startup diagnostics for the dashboard app (Arango probe lives on gateway)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib import error, request

from arango_dashboard_agent.services.genie_workspace_client import genie_workspace_auth_debug
from arango_dashboard_agent.services.genie_registry import (
    genie_server_auto_provision_enabled,
    get_active_genie_space_row,
    refresh_genie_space_id_in_app,
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _post_debug_webhook(url: str, payload: dict) -> None:
    if not url:
        return
    try:
        req = request.Request(url=url, method="POST")
        req.add_header("Content-Type", "application/json")
        body = json.dumps(payload).encode("utf-8")
        with request.urlopen(req, data=body, timeout=3):
            pass
    except (error.URLError, TimeoutError, ValueError):
        pass


def run_genie_startup_debug(app, *, retry_genie: bool = False) -> dict[str, Any]:
    """Return a payload fragment with only ``genie`` diagnostics."""
    warehouse_id = app.config["DATABRICKS_SQL_WAREHOUSE_ID"]
    genie_registry_table = (app.config.get("GENIE_SPACE_REGISTRY_TABLE") or "").strip()

    if retry_genie:
        refresh_genie_space_id_in_app(app)

    genie_block: dict[str, Any] = {
        "registry_table_configured": bool(genie_registry_table),
        "registry_table": genie_registry_table or None,
        "space_id_configured": bool((app.config.get("GENIE_SPACE_ID") or "").strip()),
        "space_id_in_config": (app.config.get("GENIE_SPACE_ID") or "").strip() or None,
        "auto_provision": genie_server_auto_provision_enabled(app.config),
        "last_provision_error": app.extensions.get("genie_last_provision_error"),
        "genie_auth": genie_workspace_auth_debug(),
    }
    if genie_registry_table and warehouse_id:
        try:
            row = get_active_genie_space_row(genie_registry_table, warehouse_id)
            genie_block["uc_active_genie_space_id"] = (
                row.get("genie_space_id") if row else None
            )
        except Exception as exc:
            genie_block["uc_read_error"] = str(exc)

    status = {
        "checked_at": _now_utc(),
        "genie": genie_block,
    }
    _post_debug_webhook(app.config.get("DEBUG_WEBHOOK_URL", ""), status)
    return status
