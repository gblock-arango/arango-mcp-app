"""AOE MCP tools backed by arango-workflow-app HTTP (no ``app.*`` imports)."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from aoe_ontoextract_mcp.workflow_client import workflow_health, workflow_request

log = logging.getLogger(__name__)


def register_http_tools(mcp: FastMCP) -> None:
    """Register OntoExtract tools that delegate to workflow-app ``/api/v1``."""

    @mcp.tool()
    def aoe_workflow_health() -> dict[str, Any]:
        """Check connectivity to arango-workflow-app (GET /health)."""
        return workflow_health()

    @mcp.tool()
    def aoe_list_ontology_library(
        limit: int = 25,
        tag: str | None = None,
    ) -> dict[str, Any]:
        """List ontologies via workflow BFF (GET /api/workflow/ontoextract/v1/ontology/library)."""
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
        if tag:
            params["tag"] = tag
        return workflow_request("GET", "ontology/library", params=params)

    @mcp.tool()
    def aoe_get_ontology_registry_entry(ontology_id: str) -> dict[str, Any]:
        """Registry metadata for one ontology via workflow BFF."""
        oid = (ontology_id or "").strip()
        if not oid:
            return {"ok": False, "error": "ontology_id is required"}
        return workflow_request("GET", f"ontology/library/{oid}")

    @mcp.tool()
    def aoe_workflow_api(
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Generic OntoExtract REST call on arango-workflow-app.

        ``path`` is relative to OntoExtract v1 (e.g. ``extraction/runs`` or ``ontology/library``).
        ``method``: GET, POST, PUT, PATCH, or DELETE.
        """
        m = (method or "GET").strip().upper()
        if m not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            return {"ok": False, "error": f"unsupported method: {method}"}
        return workflow_request(m, path, json_body=json_body)
