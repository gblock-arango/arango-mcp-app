"""AMP ADA via **arango-gateway-app** ``POST /api/arango/chat``.

Genie Code tool ``arango-ada-conversation`` calls the gateway (UC/env URL); the gateway forwards to
``ARANGO_CONVERSATION_URL`` when set (Arango Managed Platform ADA — https://docs.arango.ai/amp/).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Mapping
from urllib import error, request

from arango_dashboard_agent.services.databricks_app_http_auth import outbound_bearer_authorization_header
from arango_dashboard_agent.services.gateway_url_registry import effective_gateway_base_url

logger = logging.getLogger(__name__)


def ask_gateway_ada_conversation(
    *,
    content: str,
    conversation_id: str | None,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """POST ``{gateway}/api/arango/chat`` — gateway relays to AMP ADA when configured."""
    text = (content or "").strip()
    if not text:
        return {"ok": False, "error": "content is empty"}

    gateway = effective_gateway_base_url(config).strip().rstrip("/")
    if not gateway:
        return {
            "ok": False,
            "error": (
                "Gateway URL is not configured. Set ARANGO_GATEWAY_BASE_URL or publish "
                "arango-gateway-app to ARANGO_GATEWAY_REGISTRY_TABLE, and set "
                "ARANGO_CONVERSATION_URL on the gateway app to the AMP ADA chat endpoint."
            ),
        }

    url = f"{gateway}/api/arango/chat"
    timeout = float(config.get("ARANGO_CONVERSATION_TIMEOUT_SECONDS") or 120.0)
    payload: dict[str, Any] = {"content": text}
    if conversation_id:
        payload["conversation_id"] = str(conversation_id).strip()

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **outbound_bearer_authorization_header(config=config),
    }
    req = request.Request(url=url, data=body, method="POST", headers=headers)
    try:
        with request.urlopen(req, timeout=max(1.0, timeout)) as resp:
            raw = resp.read(65536).decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        logger.warning("Gateway ADA chat HTTP %s: %s", exc.code, detail[:500])
        return {
            "ok": False,
            "error": f"HTTP {exc.code} from gateway /api/arango/chat — {detail[:800]}",
        }
    except Exception as exc:
        logger.warning("Gateway ADA chat request failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Invalid JSON from gateway: {exc}"}

    if not isinstance(data, dict):
        return {"ok": False, "error": "Gateway response must be a JSON object"}

    if data.get("ok") is False:
        return {"ok": False, "error": str(data.get("error") or "gateway error")}

    if not data.get("conversation_id"):
        data["conversation_id"] = (conversation_id or "").strip() or str(uuid.uuid4())
    return data
