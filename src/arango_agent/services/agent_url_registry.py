"""Unity Catalog registry for the deployed arango-agent public base URL (same pattern as gateway)."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from databricks.sdk import WorkspaceClient

from arango_agent.services.databricks_sql import execute_sql
from arango_agent.services.registry_types import parse_fqn_table

logger = logging.getLogger(__name__)

_publish_lock = threading.Lock()
_uc_read_lock = threading.Lock()
_uc_read_cache: dict[str, Any] = {"key": "", "value": "", "expires": 0.0}


def _row_get_ci(row: dict[str, Any], name: str) -> Any:
    if name in row:
        return row[name]
    lower = name.lower()
    for k, v in row.items():
        if str(k).lower() == lower:
            return v
    return None


def ensure_agent_registry_table(table_name: str, warehouse_id: str) -> None:
    """Create schema/table for agent URL registry if they do not exist."""
    ref = parse_fqn_table(table_name)
    execute_sql(
        statement=f"CREATE SCHEMA IF NOT EXISTS `{ref.catalog}`.`{ref.schema}`",
        warehouse_id=warehouse_id,
    )
    execute_sql(
        statement=f"""
            CREATE TABLE IF NOT EXISTS {ref.fqn} (
                base_url STRING NOT NULL,
                app_name STRING NOT NULL,
                is_active BOOLEAN NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            USING DELTA
        """,
        warehouse_id=warehouse_id,
    )
    try_grant_account_users_agent_registry_dml(ref, warehouse_id)


def try_grant_account_users_agent_registry_dml(ref: Any, warehouse_id: str) -> None:
    """Allow non-owner identities to upsert URL rows (see gateway registry pattern)."""
    try:
        execute_sql(
            statement=f"GRANT SELECT, MODIFY ON TABLE {ref.fqn} TO `account users`",
            warehouse_id=warehouse_id,
        )
    except Exception as exc:
        logger.info(
            "Could not GRANT agent URL registry to `account users` (may be disabled or not owner): %s",
            exc,
        )


def publish_agent_base_url(
    *,
    table_name: str,
    warehouse_id: str,
    base_url: str,
    app_name: str,
) -> None:
    ref = parse_fqn_table(table_name)
    url = (base_url or "").strip().rstrip("/")
    name = (app_name or "").strip()
    if not url or not name:
        return

    try_grant_account_users_agent_registry_dml(ref, warehouse_id)
    execute_sql(
        statement=f"UPDATE {ref.fqn} SET is_active = FALSE WHERE is_active = TRUE",
        warehouse_id=warehouse_id,
    )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    safe_url = url.replace("'", "''")
    safe_name = name.replace("'", "''")
    execute_sql(
        statement=f"""
            INSERT INTO {ref.fqn}
                (base_url, app_name, is_active, updated_at)
            VALUES
                ('{safe_url}', '{safe_name}', TRUE, TIMESTAMP('{ts}'))
        """,
        warehouse_id=warehouse_id,
    )
    try_grant_account_users_agent_registry_dml(ref, warehouse_id)


def resolve_self_app_base_url() -> str | None:
    name = (os.environ.get("DATABRICKS_APP_NAME") or "").strip()
    if not name:
        return None
    try:
        app = WorkspaceClient().apps.get(name)
        u = (getattr(app, "url", None) or "").strip().rstrip("/")
        return u or None
    except Exception as exc:
        logger.warning("Could not resolve Databricks App URL for %r: %s", name, exc)
        return None


def publish_self_agent_url_to_uc_if_configured(app: Any) -> None:
    """On agent startup, upsert our public URL into UC for consumers (e.g. dashboard)."""
    if not bool(app.config.get("ARANGO_AGENT_REGISTRY_AUTO_CREATE", True)):
        return
    table = str(app.config.get("ARANGO_AGENT_REGISTRY_TABLE") or "").strip()
    warehouse = str(app.config.get("DATABRICKS_SQL_WAREHOUSE_ID") or "").strip()
    if not table or not warehouse:
        return
    url = resolve_self_app_base_url()
    if not url:
        return
    app_name = (os.environ.get("DATABRICKS_APP_NAME") or "").strip() or "unknown"
    try:
        with _publish_lock:
            ensure_agent_registry_table(table_name=table, warehouse_id=warehouse)
            publish_agent_base_url(
                table_name=table,
                warehouse_id=warehouse,
                base_url=url,
                app_name=app_name,
            )
        logger.info("Published arango-agent base URL to UC table %s", table)
    except Exception as exc:
        logger.warning("Could not publish agent URL to UC (%s): %s", table, exc)


def get_active_agent_base_url(table_name: str, warehouse_id: str) -> str | None:
    table = (table_name or "").strip()
    wid = (warehouse_id or "").strip()
    if not table or not wid:
        return None
    try:
        ref = parse_fqn_table(table)
    except ValueError:
        return None
    try:
        result = execute_sql(
            statement=f"""
                SELECT base_url
                FROM {ref.fqn}
                WHERE is_active IS TRUE
                ORDER BY updated_at DESC
                LIMIT 1
            """,
            warehouse_id=wid,
        )
    except Exception as exc:
        logger.warning("Agent URL registry read failed (%s): %s", table, exc)
        return None
    rows: list[dict[str, Any]] = result.get("rows") or []
    if not rows:
        return None
    raw = _row_get_ci(rows[0], "base_url")
    u = (str(raw) if raw is not None else "").strip().rstrip("/")
    return u or None


def _cached_uc_agent_base_url(cfg: Any) -> str:
    table = str(cfg.get("ARANGO_AGENT_REGISTRY_TABLE") or "").strip()
    wid = str(cfg.get("DATABRICKS_SQL_WAREHOUSE_ID") or "").strip()
    if not table or not wid:
        return ""
    key = f"{table}\0{wid}"
    now = time.monotonic()
    with _uc_read_lock:
        if key == _uc_read_cache["key"] and now < float(_uc_read_cache["expires"]):
            return str(_uc_read_cache["value"])
    uc = get_active_agent_base_url(table, wid) or ""
    url = uc.strip().rstrip("/")
    ttl = 20.0 if not url else 300.0
    with _uc_read_lock:
        _uc_read_cache["key"] = key
        _uc_read_cache["value"] = url
        _uc_read_cache["expires"] = now + ttl
    return url


def effective_agent_base_url(cfg: Any) -> str:
    """
    Base URL for HTTP calls to arango-agent.

    1. Non-empty ``ARANGO_AGENT_BASE_URL`` wins.
    2. Otherwise the active row in ``ARANGO_AGENT_REGISTRY_TABLE`` (cached briefly).
    """
    explicit = (cfg.get("ARANGO_AGENT_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    return _cached_uc_agent_base_url(cfg)


def invalidate_agent_url_uc_cache() -> None:
    with _uc_read_lock:
        _uc_read_cache["expires"] = 0.0
