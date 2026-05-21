"""HTTP client: AOE MCP tools → arango-workflow-app BFF (same peer-app auth as dashboard→agent)."""

from __future__ import annotations

import logging
from typing import Any

import requests

from aoe_ontoextract_mcp.config import workflow_app_base_url

logger = logging.getLogger(__name__)

_BFF_PREFIX = "/api/workflow/ontoextract/v1"


def _outbound_headers() -> dict[str, str]:
    from arango_dashboard_agent.services.databricks_app_http_auth import (
        outbound_bearer_authorization_header,
    )

    return dict(outbound_bearer_authorization_header())


def workflow_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """
    Call OntoExtract REST via the workflow BFF (JWT-exempt peer path).

    ``path`` may be ``ontology/library`` or ``/api/v1/ontology/library`` (normalized).
    """
    base = workflow_app_base_url()
    if not base:
        return {
            "ok": False,
            "error": (
                "arango-workflow-app URL is not configured. Deploy arango-workflow-app "
                "(publishes ARANGO_WORKFLOW_REGISTRY_TABLE), grant mcp-arango-agent CAN USE "
                "on that app, and set DATABRICKS_SQL_WAREHOUSE_ID on mcp-arango-agent."
            ),
        }

    p = (path or "").strip()
    if p.startswith("/api/workflow/ontoextract/v1/"):
        p = p[len("/api/workflow/ontoextract/v1/") :]
    elif p.startswith("/api/v1/"):
        p = p[len("/api/v1/") :]
    elif p.startswith("api/v1/"):
        p = p[len("api/v1/") :]
    p = p.lstrip("/")
    url = f"{base}{_BFF_PREFIX}/{p}"

    try:
        r = requests.request(
            method.upper(),
            url,
            params=params or None,
            json=json_body,
            headers=_outbound_headers(),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("workflow_request %s %s failed: %s", method, url, exc)
        return {"ok": False, "error": str(exc), "url": url}

    ct = (r.headers.get("Content-Type") or "").lower()
    if "application/json" in ct:
        try:
            body: Any = r.json()
        except ValueError:
            body = {"raw": (r.text or "")[:4000]}
    else:
        text = (r.text or "")[:4000]
        body = {
            "ok": False,
            "error": "Non-JSON response from workflow-app (often HTML login page)",
            "http_status": r.status_code,
            "content_type": r.headers.get("Content-Type"),
            "body_preview": text,
        }

    if r.status_code >= 400:
        if isinstance(body, dict):
            out = dict(body)
            out.setdefault("ok", False)
            out.setdefault("http_status", r.status_code)
            return out
        return {"ok": False, "http_status": r.status_code, "body": body}

    if isinstance(body, dict):
        return body
    return {"ok": True, "data": body}


def workflow_health(timeout: float = 30.0) -> dict[str, Any]:
    """``GET /api/workflow/health`` on workflow-app (public BFF)."""
    base = workflow_app_base_url()
    if not base:
        return {"ok": False, "error": "arango-workflow-app URL is not configured"}
    url = f"{base}/api/workflow/health"
    try:
        r = requests.get(url, headers=_outbound_headers(), timeout=timeout)
        if "application/json" in (r.headers.get("Content-Type") or "").lower():
            return r.json()
        return {"status": "unknown", "http_status": r.status_code, "body_preview": (r.text or "")[:500]}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "url": url}
