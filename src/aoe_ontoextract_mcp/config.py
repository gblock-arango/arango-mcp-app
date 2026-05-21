"""Runtime config for AOE MCP → arango-workflow-app HTTP (UC registry + env override)."""

from __future__ import annotations

import os
from typing import Any

from arango_dashboard_agent.services.workflow_url_registry import effective_workflow_base_url


def workflow_resolution_config() -> dict[str, Any]:
    """Config dict for :func:`effective_workflow_base_url` (env + UC)."""
    return {
        "ARANGO_WORKFLOW_APP_BASE_URL": (
            os.environ.get("ARANGO_WORKFLOW_APP_BASE_URL") or ""
        ).strip(),
        "ARANGO_WORKFLOW_REGISTRY_TABLE": (
            os.environ.get("ARANGO_WORKFLOW_REGISTRY_TABLE")
            or "workspace.default.arango_workflow_registry"
        ).strip(),
        "DATABRICKS_SQL_WAREHOUSE_ID": (
            os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID") or ""
        ).strip(),
    }


def workflow_app_base_url() -> str:
    """Public base URL of arango-workflow-app (no trailing slash)."""
    return effective_workflow_base_url(workflow_resolution_config())
