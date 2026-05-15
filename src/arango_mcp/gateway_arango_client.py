"""HTTP client for Arango REST via ``arango-gateway-app`` ``POST /api/arango/http``."""

from __future__ import annotations

import json
import logging
import ssl
from typing import Any, Optional

from urllib import error, request

from arango_agent.services.databricks_app_http_auth import outbound_bearer_authorization_header
from arango_mcp.config import GatewaySettings

logger = logging.getLogger(__name__)


class GatewayArangoClient:
    """HTTP transport to ``arango-gateway-app`` ``POST /api/arango/http``.

    This is the low-level **request/response** channel (including optional auth to the gateway).
    For a ``get_db()``-style API (``db.aql``, ``db.collection``, …), use
    :class:`gateway_database.GatewayDatabase` via :func:`arango_connector.ArangoDBConnector.get_db`
    in gateway mode.
    """

    def __init__(
        self,
        gateway: GatewaySettings,
        *,
        effective_base_url: str | None = None,
        outbound_bearer: str | None = None,
        auth_config: dict[str, Any] | None = None,
    ) -> None:
        self._settings = gateway
        self._effective_base_url = (
            (effective_base_url or "").strip().rstrip("/") or None
        )
        self._outbound_bearer = (outbound_bearer or "").strip() or None
        self._auth_config = auth_config or {}
        self._proxy_url: str = ""
        self._server_version: Optional[str] = None

    @property
    def server_version(self) -> Optional[str]:
        return self._server_version

    def _base(self) -> str:
        if self._effective_base_url:
            return self._effective_base_url
        return (self._settings.base_url or "").strip().rstrip("/")

    def _open_url(self, url: str, *, data: bytes | None, headers: dict[str, str]) -> Any:
        req = request.Request(url=url, method="POST", data=data)
        for k, v in headers.items():
            req.add_header(k, v)
        ssl_ctx: ssl.SSLContext | None = None
        if url.lower().startswith("https:") and not self._settings.tls_verify:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        open_kw: dict[str, Any] = {"timeout": float(self._settings.timeout_seconds)}
        if ssl_ctx is not None:
            open_kw["context"] = ssl_ctx
        return request.urlopen(req, **open_kw)

    def connect(self) -> None:
        """Resolve proxy URL and verify gateway + Arango with ``GET /_api/version``."""
        base = self._base()
        if not base:
            raise ValueError(
                "Gateway URL is unset: set ARANGO_GATEWAY_BASE_URL or publish an active row to "
                "ARANGO_GATEWAY_REGISTRY_TABLE and set DATABRICKS_SQL_WAREHOUSE_ID for UC reads "
                "(same pattern as arango-dashboard-app)."
            )
        self._proxy_url = f"{base}/api/arango/http"
        result = self.request("GET", "/_api/version", json_body=None)
        if not result.get("ok"):
            raise RuntimeError(
                f"Gateway Arango probe failed: {result.get('error')!r} "
                f"status={result.get('status_code')} body={result.get('body')}"
            )
        body = result.get("body")
        if isinstance(body, dict):
            self._server_version = str(body.get("version", "") or "") or None
        logger.info(
            "GatewayArangoClient connected via %s (Arango version=%s)",
            self._proxy_url,
            self._server_version,
        )

    def disconnect(self) -> None:
        self._proxy_url = ""
        self._server_version = None

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
    ) -> dict[str, Any]:
        """Call ``POST {gateway}/api/arango/http``; return ``{ok, status_code, body, error?}``."""
        if not self._proxy_url:
            raise RuntimeError("GatewayArangoClient not connected; call connect() first")

        m = method.upper().strip()
        norm_path = path if path.startswith("/") else f"/{path}"
        envelope: dict[str, Any] = {"method": m, "path": norm_path}
        if m != "GET" and json_body is not None:
            envelope["body"] = json_body

        raw = json.dumps(envelope).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **outbound_bearer_authorization_header(
                config=self._auth_config,
                override_token=self._outbound_bearer,
            ),
        }

        try:
            with self._open_url(self._proxy_url, data=raw, headers=headers) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                parsed: Any = json.loads(text) if text.strip() else {}
                if not isinstance(parsed, dict):
                    return {
                        "ok": False,
                        "status_code": resp.getcode(),
                        "body": {},
                        "error": "gateway returned non-object JSON",
                    }
                return parsed
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text.strip() else {}
            except json.JSONDecodeError:
                parsed = {"raw": text}
            if isinstance(parsed, dict) and "ok" in parsed:
                return parsed
            return {
                "ok": False,
                "status_code": exc.code,
                "body": parsed if isinstance(parsed, dict) else {},
                "error": str(exc.reason),
            }
        except Exception as exc:
            logger.error("Gateway HTTP error: %s", exc, exc_info=True)
            return {
                "ok": False,
                "status_code": None,
                "body": {},
                "error": str(exc),
            }

    def health_check(self) -> bool:
        try:
            r = self.request("GET", "/_api/version", json_body=None)
            return bool(r.get("ok"))
        except Exception:
            return False
