"""Unity Catalog registry for the deployed arango-agent public base URL (same pattern as gateway)."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from databricks.sdk import WorkspaceClient

from arango_agent.services.databricks_sql import execute_sql
from arango_agent.services.registry_types import parse_fqn_table

logger = logging.getLogger(__name__)

_publish_lock = threading.Lock()
_uc_read_lock = threading.Lock()
_uc_read_cache: dict[str, Any] = {"key": "", "value": "", "expires": 0.0}

# Substrings (lowercased) of Delta concurrent-write errors that justify retrying MERGE.
_DELTA_CONCURRENT_MARKERS = (
    "concurrent",
    "concurrentappend",
    "concurrentmodification",
    "concurrent_append",
    "concurrent_modification",
    "concurrent_delete_read",
    "concurrent_delete_delete",
    "concurrent_transaction",
    "concurrent_write",
)


def _looks_like_delta_concurrent_conflict(exc: Exception) -> bool:
    """Heuristic match for Delta optimistic-concurrency conflicts surfaced by the SQL API."""
    text = str(exc).lower()
    return any(marker in text for marker in _DELTA_CONCURRENT_MARKERS)


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
    max_merge_retries: int = 8,
) -> None:
    """
    Idempotently mark this app's base URL as the single active row.

    Uses a single ``MERGE INTO`` so that concurrent writers (e.g. multiple gunicorn
    workers calling ``publish_self_agent_url_to_uc_if_configured`` on startup, or the
    deploy script's ``update_arango_agent_registry_uc.sh`` running in parallel) cannot
    leave duplicate active rows. The merge:
      - inserts the row when no row with this ``base_url`` exists,
      - re-activates and refreshes ``updated_at`` when a row with this URL exists,
      - sets every other ``is_active=TRUE`` row to ``FALSE`` (``WHEN NOT MATCHED BY SOURCE``).

    The in-process ``threading.Lock`` above only protects multiple threads inside
    one gunicorn worker; this MERGE + retry is what guarantees correctness across
    worker processes (and across deploy-script and app-startup writers).
    """
    ref = parse_fqn_table(table_name)
    url = (base_url or "").strip().rstrip("/")
    name = (app_name or "").strip()
    if not url or not name:
        return

    try_grant_account_users_agent_registry_dml(ref, warehouse_id)
    safe_url = url.replace("'", "''")
    safe_name = name.replace("'", "''")

    merge_sql = f"""
        MERGE INTO {ref.fqn} t
        USING (
            SELECT
                '{safe_url}' AS base_url,
                '{safe_name}' AS app_name,
                current_timestamp() AS updated_at
        ) s
        ON t.base_url = s.base_url
        WHEN MATCHED THEN UPDATE SET
            app_name = s.app_name,
            is_active = TRUE,
            updated_at = s.updated_at
        WHEN NOT MATCHED THEN INSERT
            (base_url, app_name, is_active, updated_at)
            VALUES (s.base_url, s.app_name, TRUE, s.updated_at)
        WHEN NOT MATCHED BY SOURCE AND t.is_active = TRUE THEN UPDATE SET
            is_active = FALSE,
            updated_at = current_timestamp()
    """

    last_exc: Exception | None = None
    for attempt in range(1, max(1, max_merge_retries) + 1):
        try:
            execute_sql(statement=merge_sql, warehouse_id=warehouse_id)
            try_grant_account_users_agent_registry_dml(ref, warehouse_id)
            return
        except Exception as exc:
            last_exc = exc
            if attempt >= max_merge_retries or not _looks_like_delta_concurrent_conflict(exc):
                raise
            backoff = 0.25 * attempt
            logger.warning(
                "Concurrent MERGE conflict on %s (attempt %d/%d); retrying in %.2fs: %s",
                ref.fqn,
                attempt,
                max_merge_retries,
                backoff,
                exc,
            )
            time.sleep(backoff)
    if last_exc is not None:
        raise last_exc


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
