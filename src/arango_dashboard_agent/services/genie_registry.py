"""
Unity Catalog–backed Genie Space ID and optional auto-provisioning.

Keeps Genie-specific persistence next to :mod:`genie_conversation` so this package can
be lifted into a separate App later with minimal coupling to Arango registry code.

Resolution (see :func:`bootstrap_genie_space_id_from_uc`):

1. Non-empty ``GENIE_SPACE_ID`` from the environment wins.
2. Otherwise read ``GENIE_SPACE_REGISTRY_TABLE`` (when configured).
3. If still missing and server auto-provision is enabled (default), create a space via the Genie
   API as the app service principal and upsert its id into UC. Opt out with ``GENIE_DISABLE_AUTO_PROVISION=1``
   or legacy ``GENIE_AUTO_PROVISION=false``.

Optional **strict** UC repair (``get_space`` with strict classification): ``POST /api/deploy/reconcile-genie``
on the deployed app (must authenticate with a token accepted by ``*.databricksapps.com`` — a workspace
PAT is often rejected). Normal flow does not need this: ``bootstrap_genie_space_id_from_uc`` at app
startup runs ``refresh_genie_space_id_in_app`` (same idea as flask-arango-mcp-app ``GENIE_AUTO_PROVISION``).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Mapping

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError

from arango_dashboard_agent.services.genie_workspace_client import agent_workspace_client
from arango_dashboard_agent.services.registry_types import RegistryTableRef, parse_fqn_table
from arango_dashboard_agent.services.databricks_sql import execute_sql

logger = logging.getLogger(__name__)

# Genie / UC registry: only deactivate rows when churn would help. Errors that mention
# ``aclPath`` / ``aclpath`` are workspace-node permission gaps for the **current** principal
# (often fixable via workspace browse, or *Genie Space* app resource) — **not** a wrong
# ``genie_space_id`` in UC; deactivating wipes ``is_active`` and makes Genie unusable.
_GENIE_SPACE_ACL_MARKERS_NO_ACLPATH: tuple[str, ...] = (
    "read permission",
    "does not have read",
    "permission denied",
    "insufficient permissions",
)

_GENIE_SPACE_GONE_MARKERS: tuple[str, ...] = (
    "not found",
    "does not exist",
    "resource does not exist",
)


def _genie_workspace_node_acl_error(low: str) -> bool:
    return "aclpath" in low


def _genie_error_should_deactivate_uc_registry_row(error_message: str) -> bool:
    """
    True when the active UC ``genie_space_id`` should be marked inactive.

    False for workspace ``aclpath`` read errors — those require grants / app resources, not
    registry edits (same symptom as PAT-owned spaces but the fix is different).
    """
    low = (error_message or "").lower()
    if _genie_workspace_node_acl_error(low):
        return False
    if any(m in low for m in _GENIE_SPACE_GONE_MARKERS):
        return True
    if any(m in low for m in _GENIE_SPACE_ACL_MARKERS_NO_ACLPATH):
        return True
    if "unable to get space" in low and (
        "permission" in low or "does not have read" in low
    ):
        return True
    return False


def genie_error_indicates_space_acl_forbidden(error_message: str) -> bool:
    """True when a Genie API failure should deactivate the UC registry row (chat retry path)."""
    return _genie_error_should_deactivate_uc_registry_row(error_message)


def invalidate_genie_space_after_acl_error(app: Any, *, space_id: str, error_message: str) -> bool:
    """
    Deactivate the UC registry row for ``space_id`` when the error indicates a **stale**
    or **wrong-owner** id — not workspace ``aclpath`` read denials (those need grants / app
    resources; deactivating only clears ``is_active`` and breaks the registry).
    """
    if not genie_error_indicates_space_acl_forbidden(error_message):
        return False
    sid = (space_id or "").strip()
    if not sid:
        return False
    table = str(app.config.get("GENIE_SPACE_REGISTRY_TABLE") or "").strip()
    wh = str(app.config.get("DATABRICKS_SQL_WAREHOUSE_ID") or "").strip()
    if not table or not wh:
        return False
    try:
        deactivate_genie_space_id_in_registry(table, wh, sid)
    except Exception as exc:
        logger.warning("Failed to deactivate Genie registry row after ACL error: %s", exc)
        return False
    app.config.pop("GENIE_SPACE_ID", None)
    app.extensions.pop("genie_space_verified_sid", None)
    logger.info(
        "Deactivated Genie space %s in UC after permission error; retry may auto-provision",
        sid,
    )
    return True


def genie_server_auto_provision_enabled(config: Mapping[str, Any]) -> bool:
    """
    Whether the app should call the Genie API to create a space when UC has no usable id.

    Default **on**. Opt out with ``GENIE_DISABLE_AUTO_PROVISION=1`` (or ``true`` / ``yes``),
    or legacy ``GENIE_AUTO_PROVISION=false`` in the environment / ``app.yaml``.
    """
    if (os.environ.get("GENIE_DISABLE_AUTO_PROVISION") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return False
    if bool(config.get("GENIE_DISABLE_AUTO_PROVISION", False)):
        return False
    return bool(config.get("GENIE_AUTO_PROVISION", True))


# Default Genie space payload when GENIE_SERIALIZED_SPACE / _FILE are unset.
# Databricks requires ``version: 2`` for new spaces (v1 triggers "export format has changed").
# See https://docs.databricks.com/aws/en/genie/conversation-api — validation rules for IDs / sorting.
_MINIMAL_GENIE_SPACE_OBJ: dict[str, Any] = {
    "version": 2,
    "config": {
        "sample_questions": [
            {
                # 32-char lowercase hex (required shape per Genie serialized_space validation)
                "id": "00000000000000010000000000000001",
                "question": [
                    "Replace sample questions and data_sources via GENIE_SERIALIZED_SPACE or GENIE_SERIALIZED_SPACE_FILE."
                ],
            }
        ]
    },
    "data_sources": {"tables": [], "metric_views": []},
}


def _minimal_genie_serialized_space_json() -> str:
    return json.dumps(_MINIMAL_GENIE_SPACE_OBJ, separators=(",", ":"))


def _parse_registry_table(table_name: str) -> RegistryTableRef:
    return parse_fqn_table(table_name)


def _row_is_active(row: dict) -> bool:
    v = row.get("is_active")
    if v is True:
        return True
    if v is False or v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "t", "yes")
    if isinstance(v, (int, float)):
        return int(v) == 1
    return False


def ensure_genie_registry_table(table_name: str, warehouse_id: str) -> None:
    """Create schema/table for Genie space id registry if they do not exist."""
    ref = _parse_registry_table(table_name)

    execute_sql(
        statement=f"CREATE SCHEMA IF NOT EXISTS `{ref.catalog}`.`{ref.schema}`",
        warehouse_id=warehouse_id,
    )

    execute_sql(
        statement=f"""
            CREATE TABLE IF NOT EXISTS {ref.fqn} (
                genie_space_id STRING NOT NULL,
                is_active BOOLEAN NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            USING DELTA
        """,
        warehouse_id=warehouse_id,
    )


def get_active_genie_space_row(table_name: str, warehouse_id: str) -> dict | None:
    """Return the newest row with ``is_active`` true, or None."""
    ref = _parse_registry_table(table_name)
    result = execute_sql(
        statement=f"""
            SELECT genie_space_id, is_active, updated_at
            FROM {ref.fqn}
            WHERE is_active IS TRUE
            ORDER BY updated_at DESC
            LIMIT 1
        """,
        warehouse_id=warehouse_id,
    )
    rows = result.get("rows", [])
    if not rows:
        return None
    row = rows[0]
    if not _row_is_active(row):
        return None
    return row


def deactivate_genie_space_id_in_registry(
    table_name: str, warehouse_id: str, genie_space_id: str
) -> None:
    """Mark rows for ``genie_space_id`` inactive (used when the app identity cannot read the space)."""
    ref = _parse_registry_table(table_name)
    gid = (genie_space_id or "").strip().replace("'", "''")
    if not gid:
        return
    execute_sql(
        statement=(
            f"UPDATE {ref.fqn} SET is_active = FALSE "
            f"WHERE genie_space_id = '{gid}' AND is_active = TRUE"
        ),
        warehouse_id=warehouse_id,
    )


def upsert_genie_registry_entry(
    table_name: str, warehouse_id: str, genie_space_id: str
) -> None:
    """
    Insert one active row for ``genie_space_id``, then mark all other rows inactive.

    Inserts **before** the bulk deactivate so a failed statement never leaves the table
    with zero active rows (the old order was UPDATE-all-false then INSERT).
    """
    ref = _parse_registry_table(table_name)
    gid = (genie_space_id or "").strip().replace("'", "''")
    if not gid:
        raise ValueError("genie_space_id is empty")

    execute_sql(
        statement=f"""
            INSERT INTO {ref.fqn} (genie_space_id, is_active, updated_at)
            VALUES ('{gid}', TRUE, current_timestamp())
        """,
        warehouse_id=warehouse_id,
    )
    execute_sql(
        statement=(
            f"UPDATE {ref.fqn} SET is_active = FALSE "
            f"WHERE genie_space_id <> '{gid}' AND is_active IS TRUE"
        ),
        warehouse_id=warehouse_id,
    )


def ensure_active_genie_registry_row(
    table_name: str, warehouse_id: str, genie_space_id: str
) -> None:
    """
    If the newest active UC row already matches ``genie_space_id``, no-op.

    Otherwise upserts so exactly that id is active (repairs missing or mismatched registry rows).
    """
    gid = (genie_space_id or "").strip()
    if not gid:
        return
    row = get_active_genie_space_row(table_name, warehouse_id)
    if row and str(row.get("genie_space_id") or "").strip() == gid:
        return
    upsert_genie_registry_entry(table_name, warehouse_id, gid)


def _active_genie_space_id(
    table_name: str, warehouse_id: str, *, auto_create_table: bool
) -> str:
    if auto_create_table:
        ensure_genie_registry_table(table_name, warehouse_id)
    row = get_active_genie_space_row(table_name, warehouse_id)
    if not row:
        return ""
    return str(row.get("genie_space_id") or "").strip()


def read_genie_space_id_from_uc(
    *,
    table_name: str,
    warehouse_id: str,
    auto_create_table: bool,
) -> str:
    """
    Load active ``genie_space_id`` from UC, or return empty string if unavailable.

    Logs a warning on failure; does not raise (startup should remain usable without Genie).
    """
    try:
        return _active_genie_space_id(
            table_name,
            warehouse_id,
            auto_create_table=auto_create_table,
        )
    except Exception as exc:
        logger.warning("Genie UC registry read failed: %s", exc)
        return ""


def resolve_genie_space_id_for_app(config: Mapping[str, Any]) -> str:
    """
    Effective Genie space id: process ``GENIE_SPACE_ID`` env first, else UC registry.

    Only ``os.environ`` is consulted for ``GENIE_SPACE_ID`` so a value copied into
    ``app.config`` from an earlier UC read does not mask registry updates (e.g. after
    deactivating an unreadable space id).
    """
    env_id = (os.environ.get("GENIE_SPACE_ID") or "").strip()
    if env_id:
        return env_id

    table = str(config.get("GENIE_SPACE_REGISTRY_TABLE") or "").strip()
    warehouse_id = str(config.get("DATABRICKS_SQL_WAREHOUSE_ID") or "").strip()
    if not table or not warehouse_id:
        return ""

    auto_create = bool(config.get("GENIE_SPACE_REGISTRY_AUTO_CREATE", True))
    return read_genie_space_id_from_uc(
        table_name=table,
        warehouse_id=warehouse_id,
        auto_create_table=auto_create,
    )


def _serialized_space_from_config(config: Mapping[str, Any]) -> str:
    path = str(config.get("GENIE_SERIALIZED_SPACE_FILE") or "").strip()
    if path:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            raise ValueError(f"GENIE_SERIALIZED_SPACE_FILE is empty: {path}")
        return raw
    raw = str(config.get("GENIE_SERIALIZED_SPACE") or "").strip()
    if raw:
        return raw
    return _minimal_genie_serialized_space_json()


@contextlib.contextmanager
def _genie_provision_lock(config: Mapping[str, Any] | None = None) -> Iterator[None]:
    path = ""
    if config is not None:
        path = str(config.get("GENIE_PROVISION_LOCK_PATH") or "").strip()
    if not path:
        path = os.environ.get("GENIE_PROVISION_LOCK_PATH", "").strip()
    if not path:
        path = "/tmp/flask_arango_genie_provision.lock"
    lock_f = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        lock_f.close()


def provision_genie_space_idempotent(
    config: Mapping[str, Any],
    *,
    workspace_client: WorkspaceClient | None = None,
) -> str:
    """
    Ensure UC has an active Genie space id: read UC, or create via API and upsert.

    Uses a file lock so multiple Gunicorn workers do not create duplicate spaces.
    Raises on unrecoverable API/SQL errors (CLI should exit non-zero).
    """
    table = str(config.get("GENIE_SPACE_REGISTRY_TABLE") or "").strip()
    warehouse_id = str(config.get("DATABRICKS_SQL_WAREHOUSE_ID") or "").strip()
    if not table or not warehouse_id:
        raise ValueError(
            "GENIE_SPACE_REGISTRY_TABLE and DATABRICKS_SQL_WAREHOUSE_ID are required "
            "to provision a Genie space"
        )

    auto_create = bool(config.get("GENIE_SPACE_REGISTRY_AUTO_CREATE", True))
    existing = _active_genie_space_id(
        table, warehouse_id, auto_create_table=auto_create
    )
    if existing:
        return existing

    serialized = _serialized_space_from_config(config)
    title_base = (
        str(config.get("GENIE_SPACE_TITLE") or "Genie (Arango agent)").strip()
        or "Genie (Arango agent)"
    )
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = f"{title_base} — {created_at}"
    description = str(config.get("GENIE_SPACE_DESCRIPTION") or "").strip() or None
    parent_raw = str(config.get("GENIE_SPACE_PARENT_PATH") or "").strip()
    parent_path = parent_raw or None

    client = workspace_client or agent_workspace_client()

    with _genie_provision_lock(config):
        again = _active_genie_space_id(
            table, warehouse_id, auto_create_table=auto_create
        )
        if again:
            return again

        create = getattr(client.genie, "create_space", None)
        if create is None:
            raise RuntimeError(
                "Databricks SDK is too old: genie.create_space is missing. "
                "Add a root requirements.txt with databricks-sdk>=0.74.0 (Databricks Apps "
                "uses pip when that file exists) or align pyproject.toml + uv.lock; then redeploy."
            )
        try:
            space = create(
                warehouse_id=warehouse_id,
                serialized_space=serialized,
                title=title,
                description=description,
                parent_path=parent_path,
            )
        except DatabricksError as exc:
            logger.warning("Genie create_space failed: %s", exc)
            raise

        sid = (getattr(space, "space_id", None) or "").strip()
        if not sid:
            raise RuntimeError("Genie create_space returned empty space_id")
        upsert_genie_registry_entry(table, warehouse_id, sid)
        logger.info("Provisioned Genie space %s and upserted UC registry", sid)
        return sid


def _genie_space_read_verify_enabled() -> bool:
    v = (os.environ.get("GENIE_VERIFY_GENIE_SPACE_READABLE") or "true").strip().lower()
    return v not in ("0", "false", "no", "")


GenieGetSpaceRegistryOutcome = Literal["ok", "deactivate", "workspace_acl", "error"]


def classify_genie_get_space_for_registry(
    workspace_client: WorkspaceClient,
    space_id: str,
    *,
    strict: bool = False,
) -> tuple[GenieGetSpaceRegistryOutcome, str | None]:
    """
    Classify ``get_space`` for how the app should treat the UC registry row.

    ``workspace_acl`` means the error references a workspace node (``aclpath``): do **not**
    deactivate UC; fix workspace browse / Genie Space app resource instead.

    When ``strict`` is True (deploy reconcile), unknown errors become ``error`` instead of
    being treated as success.
    """
    sid = (space_id or "").strip()
    if not sid:
        return "deactivate", "space_id is empty"
    try:
        workspace_client.genie.get_space(sid, include_serialized_space=False)
        return "ok", None
    except DatabricksError as exc:
        msg = str(exc)
        low = msg.lower()
        if _genie_workspace_node_acl_error(low):
            return "workspace_acl", msg
        if _genie_error_should_deactivate_uc_registry_row(msg):
            return "deactivate", msg
        logger.warning("Genie get_space inconclusive (not deactivating registry): %s", exc)
        if strict:
            return "error", msg
        return "ok", None
    except Exception as exc:
        logger.warning("Genie get_space unexpected error (not deactivating registry): %s", exc)
        if strict:
            return "error", str(exc)
        return "ok", None


def reconcile_genie_uc_registry_for_dashboard_app(app: Any) -> dict[str, Any]:
    """
    Deploy hook: validate or create a Genie space using **this app's** ``WorkspaceClient`` (Genie
    API only — SQL registry writes still use :mod:`databricks_sql`).

    1. Resolve the candidate space id (``GENIE_SPACE_ID`` env wins, else UC active row).
    2. ``get_space`` with strict classification: if the app identity can read it, ensure UC has
       exactly one active row for that id.
    3. Otherwise deactivate that id in UC (when not blocked by ``GENIE_SPACE_ID`` env) and
       ``create_space`` when server auto-provision is enabled.

    Databricks does not expose a stable "owner principal" on Genie spaces; **readability via
    ``get_space`` under the app OAuth client** is the supported check that the space is usable
    for this dashboard app.
    """
    from arango_dashboard_agent.services.genie_workspace_client import running_in_databricks_app

    out: dict[str, Any] = {
        "databricks_app_runtime_detected": running_in_databricks_app(),
    }
    table = str(app.config.get("GENIE_SPACE_REGISTRY_TABLE") or "").strip()
    wh = str(app.config.get("DATABRICKS_SQL_WAREHOUSE_ID") or "").strip()
    if not table or not wh:
        out["ok"] = False
        out["error"] = (
            "GENIE_SPACE_REGISTRY_TABLE or DATABRICKS_SQL_WAREHOUSE_ID is not set in app config."
        )
        return out

    auto_create = bool(app.config.get("GENIE_SPACE_REGISTRY_AUTO_CREATE", True))
    if auto_create:
        ensure_genie_registry_table(table, wh)

    client = agent_workspace_client()
    cfg = dict(app.config)
    env_override = (os.environ.get("GENIE_SPACE_ID") or "").strip()
    resolved = resolve_genie_space_id_for_app(app.config)

    if resolved:
        outcome, why = classify_genie_get_space_for_registry(
            client, resolved, strict=True
        )
        if outcome == "ok":
            ensure_active_genie_registry_row(table, wh, resolved)
            refresh_genie_space_id_in_app(app)
            out["ok"] = True
            out["space_id"] = resolved
            out["action"] = "validated_registry"
            out["detail"] = (
                "App identity can read this Genie space; UC registry has an active row for it."
            )
            return out

        if env_override == resolved:
            out["ok"] = False
            out["error"] = (
                f"GENIE_SPACE_ID={resolved!r} is not readable by this app's identity. "
                f"Remove the env override in the Databricks App settings, then redeploy. ({why})"
            )
            return out

        if outcome == "workspace_acl":
            deactivate_genie_space_id_in_registry(table, wh, resolved)
        elif outcome in ("deactivate", "error"):
            deactivate_genie_space_id_in_registry(table, wh, resolved)
        else:
            out["ok"] = False
            out["error"] = f"Unexpected Genie get_space outcome {outcome!r}: {why}"
            return out

        if not genie_server_auto_provision_enabled(app.config):
            refresh_genie_space_id_in_app(app)
            out["ok"] = False
            out["error"] = (
                "Existing Genie space is not usable for this app and "
                "GENIE_DISABLE_AUTO_PROVISION is set — cannot create a replacement."
            )
            return out

        sid = provision_genie_space_idempotent(cfg, workspace_client=client)
        refresh_genie_space_id_in_app(app)
        out["ok"] = True
        out["space_id"] = sid
        out["action"] = "replaced_space"
        out["detail"] = (
            f"Previous id was not readable by this app ({outcome}); created new space and UC row."
        )
        return out

    if not genie_server_auto_provision_enabled(app.config):
        refresh_genie_space_id_in_app(app)
        out["ok"] = False
        out["error"] = (
            "No Genie space id in UC and GENIE_DISABLE_AUTO_PROVISION is set — nothing to reconcile."
        )
        return out

    sid = provision_genie_space_idempotent(cfg, workspace_client=client)
    refresh_genie_space_id_in_app(app)
    out["ok"] = True
    out["space_id"] = sid
    out["action"] = "created_space"
    out["detail"] = "UC had no active row; created Genie space as this app and upserted UC."
    return out


def provision_genie_space_cli() -> int:
    """
    Entry point for ``arango_dashboard_agent.provision_genie_uc`` / ``update_genie_registry_uc.sh``.

    Always runs idempotent provision (ignores ``GENIE_AUTO_PROVISION``).
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from arango_mcp.config import genie_cli_config_dict

    cfg = genie_cli_config_dict()
    table = str(cfg.get("GENIE_SPACE_REGISTRY_TABLE") or "").strip()
    wh = str(cfg.get("DATABRICKS_SQL_WAREHOUSE_ID") or "").strip()
    if not table or not wh:
        logger.error(
            "GENIE_SPACE_REGISTRY_TABLE and DATABRICKS_SQL_WAREHOUSE_ID must be set"
        )
        return 1
    try:
        # Shell / CI: use unified env auth (M2M or PAT) as configured by update_genie_registry_uc.sh.
        # Do not use agent_workspace_client() here — that path is for the running App runtime.
        sid = provision_genie_space_idempotent(cfg, workspace_client=WorkspaceClient())
    except Exception as exc:
        logger.error("Genie provision failed: %s", exc)
        return 1
    print(sid)
    return 0


def refresh_genie_space_id_in_app(app: Any) -> str:
    """
    Resolve ``GENIE_SPACE_ID`` from UC or auto-provision, updating ``app.config``.

    Used at startup (via :func:`bootstrap_genie_space_id_from_uc`) and lazily from
    ``/api/genie/chat`` so a slow warehouse or race does not leave the UI stuck forever.
    """
    err_key = "genie_last_provision_error"
    verified_key = "genie_space_verified_sid"

    env_sid = (os.environ.get("GENIE_SPACE_ID") or "").strip()
    if env_sid:
        app.config["GENIE_SPACE_ID"] = env_sid
        app.extensions[verified_key] = env_sid
        app.extensions.pop(err_key, None)
        return env_sid

    table = str(app.config.get("GENIE_SPACE_REGISTRY_TABLE") or "").strip()
    wh = str(app.config.get("DATABRICKS_SQL_WAREHOUSE_ID") or "").strip()
    if not table or not wh:
        app.extensions[err_key] = (
            "GENIE_SPACE_REGISTRY_TABLE or DATABRICKS_SQL_WAREHOUSE_ID is empty in app config."
        )
        return ""

    resolved = resolve_genie_space_id_for_app(app.config)
    if resolved and _genie_space_read_verify_enabled():
        if app.extensions.get(verified_key) != resolved:
            client = agent_workspace_client()
            outcome, why = classify_genie_get_space_for_registry(client, resolved)
            if outcome == "ok":
                app.extensions[verified_key] = resolved
            elif outcome == "workspace_acl":
                logger.error(
                    "Genie get_space failed with workspace node ACL (aclpath). "
                    "Leaving the UC registry row active — grant this app's principal read/browse "
                    "on that workspace path or attach a Genie Space resource to the Databricks App. %s",
                    why,
                )
                app.extensions[verified_key] = resolved
            elif outcome in ("deactivate", "error"):
                logger.warning(
                    "Genie space %s is not usable for this app; deactivating UC registry row. %s",
                    resolved,
                    why,
                )
                try:
                    deactivate_genie_space_id_in_registry(table, wh, resolved)
                except Exception as exc:
                    logger.error("Failed to deactivate bad Genie registry row: %s", exc)
                app.config.pop("GENIE_SPACE_ID", None)
                app.extensions.pop(verified_key, None)
                resolved = ""

    elif resolved:
        app.extensions[verified_key] = resolved

    if not resolved:
        # UC has no usable active row; drop any stale in-process id so we do not chat with a
        # ghost id after UC no longer has an active row or a failed upsert.
        app.config.pop("GENIE_SPACE_ID", None)
        app.extensions.pop(verified_key, None)

    if resolved:
        app.config["GENIE_SPACE_ID"] = resolved
        app.extensions.pop(err_key, None)
        return resolved

    if not genie_server_auto_provision_enabled(app.config):
        app.extensions.pop(err_key, None)
        return ""

    cfg = dict(app.config)
    try:
        sid = provision_genie_space_idempotent(cfg, workspace_client=None)
        if sid:
            app.config["GENIE_SPACE_ID"] = sid
            app.extensions[verified_key] = sid
            app.extensions.pop(err_key, None)
            return sid
        app.extensions[err_key] = "Genie provision returned empty space id."
        return ""
    except Exception as exc:
        logger.error("Genie auto-provision failed: %s", exc, exc_info=True)
        app.extensions[err_key] = str(exc)
        return ""


def bootstrap_genie_space_id_from_uc(app: Any) -> None:
    """
    Set ``app.config['GENIE_SPACE_ID']`` from UC and optionally auto-provision.

    ``app`` is the Flask instance; typed as ``Any`` to avoid importing Flask here.
    """
    refresh_genie_space_id_in_app(app)
