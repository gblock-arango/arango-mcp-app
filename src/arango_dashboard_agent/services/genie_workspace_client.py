"""Construct :class:`databricks.sdk.WorkspaceClient` for Genie HTTP APIs.

**Databricks Apps (managed runtime):** each app has its own dedicated service principal. The
platform injects ``DATABRICKS_CLIENT_ID`` / ``DATABRICKS_CLIENT_SECRET`` for *that* app only;
see https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth . Genie uses
:func:`agent_workspace_client` which resolves to plain ``WorkspaceClient()`` in the App
runtime so the SDK applies **app authorization** — the same pattern as ``arango-gateway-app``,
but with the **arango-mcp-app** app's SP (each deployment is separate; gateway and agent identities
are not mixed in code).
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from typing import Any

from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)

_genie_oauth_env_lock = threading.Lock()


@contextlib.contextmanager
def _oauth_m2m_env_removed() -> None:
    """Temporarily unset OAuth M2M env vars so :class:`WorkspaceClient` cannot select oauth-m2m."""
    keys = ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET")
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _has_oauth_m2m_env() -> bool:
    """True when env looks configured for confidential OAuth (host + app or workload client pair)."""
    return bool(
        (os.environ.get("DATABRICKS_HOST") or "").strip()
        and (os.environ.get("DATABRICKS_CLIENT_ID") or "").strip()
        and (os.environ.get("DATABRICKS_CLIENT_SECRET") or "").strip()
    )


def _running_in_databricks_app() -> bool:
    """
    True when this process is almost certainly a Databricks App (not a laptop shell).

    The platform does not always set ``DATABRICKS_APP_NAME`` / ``DATABRICKS_APP_PORT`` in the
    worker environment; managed apps often use ``/app/python/source_code`` as the deploy root.
    When in doubt, presence of **both** ``DATABRICKS_CLIENT_ID`` and ``DATABRICKS_CLIENT_SECRET``
    together with ``DATABRICKS_HOST`` and a typical Apps filesystem layout is a strong signal.
    """
    if (os.environ.get("DATABRICKS_APP_PORT") or "").strip():
        return True
    if (os.environ.get("DATABRICKS_APP_NAME") or "").strip():
        return True
    sc = (os.environ.get("DATABRICKS_SOURCE_CODE_PATH") or "").strip().lower()
    if sc and "/app/python" in sc:
        return True
    try:
        cwd = os.getcwd().lower()
    except OSError:
        cwd = ""
    if "/app/python" in cwd:
        return True
    # Apps inject the app SP's OAuth pair; laptops rarely use both + workspace host + /app cwd.
    if _has_oauth_m2m_env():
        try:
            home = (os.environ.get("HOME") or "").strip().lower()
        except Exception:
            home = ""
        if home == "/home/app" or home == "/app":
            return True
        if cwd.startswith("/app/"):
            return True
    return False


def running_in_databricks_app() -> bool:
    """True when this process is running inside a Databricks App worker (public alias)."""
    return _running_in_databricks_app()


def _genie_use_app_runtime_auth_for_sdk() -> bool:
    v = (os.environ.get("GENIE_USE_APP_RUNTIME_AUTH_FOR_SDK") or "true").strip().lower()
    return v not in ("0", "false", "no", "")


def agent_workspace_client() -> WorkspaceClient:
    """
    Workspace client for **Genie** REST calls (not SQL warehouse — see module docstring).

    In a **Databricks App**, uses plain ``WorkspaceClient()`` so the SDK authenticates with the
    app-injected OAuth client (this app's service principal — never the gateway app's).

    **Off-platform** (laptop) with ``DATABRICKS_HOST`` + client id + secret **and**
    ``DATABRICKS_TOKEN`` (PAT): strips the OAuth pair so ``WorkspaceClient(host, token)`` can drive
    Genie as your user while SQL may still use M2M. **Databricks Apps** usually have no PAT; if
    runtime detection misses (some platforms omit ``DATABRICKS_APP_*``), stripping would break
    ``create_space`` — we only strip when a PAT is present. Set
    ``GENIE_USE_APP_RUNTIME_AUTH_FOR_SDK=false`` to always use default env for Genie.

    SQL paths use :mod:`databricks_sql` and are unaffected.

    Default Gunicorn **sync** workers handle one request at a time per process; if you use
    **gthread**, avoid concurrent Genie + SQL while the strip path mutates ``os.environ`` (rare).
    """
    if not _genie_use_app_runtime_auth_for_sdk():
        return WorkspaceClient()

    if _running_in_databricks_app():
        logger.info(
            "Genie (agent): WorkspaceClient() in Databricks App runtime (app service principal via SDK)."
        )
        return WorkspaceClient()

    if not _has_oauth_m2m_env():
        return WorkspaceClient()

    host = (os.environ.get("DATABRICKS_HOST") or "").strip()
    token = (os.environ.get("DATABRICKS_TOKEN") or "").strip()
    if not token:
        # Apps inject M2M without a PAT; stripping CLIENT_ID/SECRET here yields an unauthenticated
        # client and Genie auto-provision never writes UC (flask-arango-mcp-app uses plain WorkspaceClient).
        logger.info(
            "Genie: OAuth env without DATABRICKS_TOKEN — using default WorkspaceClient "
            "(Databricks App SP or M2M-only local; no PAT override)."
        )
        return WorkspaceClient()

    with _genie_oauth_env_lock:
        with _oauth_m2m_env_removed():
            try:
                logger.info(
                    "Genie: OAuth M2M env vars removed for WorkspaceClient construction "
                    "(local / non-App; GENIE_USE_APP_RUNTIME_AUTH_FOR_SDK=true)."
                )
                return WorkspaceClient(host=host or None)
            except ValueError as exc:
                logger.warning(
                    "Genie host-only WorkspaceClient (OAuth env removed) failed: %s", exc,
                )

        if host and token:
            with _oauth_m2m_env_removed():
                try:
                    logger.info("Genie (agent): WorkspaceClient with DATABRICKS_TOKEN (OAuth env removed).")
                    return WorkspaceClient(host=host, token=token)
                except ValueError as exc:
                    logger.warning("Genie token WorkspaceClient failed: %s", exc)

    logger.warning(
        "Genie: falling back to default WorkspaceClient (oauth-m2m). "
        "If Genie shows aclPath read errors on a Databricks App, confirm the process is detected "
        "as an App runtime (see startup debug genie_auth) or attach a Genie Space resource."
    )
    return WorkspaceClient()


def genie_workspace_auth_debug() -> dict[str, Any]:
    """Non-secret fields for ``/api/debug/startup-status`` — confirms Genie auth path."""
    info: dict[str, Any] = {
        "databricks_app_runtime_detected": running_in_databricks_app(),
        "genie_use_app_runtime_auth_for_sdk": _genie_use_app_runtime_auth_for_sdk(),
    }
    cid = (os.environ.get("DATABRICKS_CLIENT_ID") or "").strip()
    if len(cid) >= 4:
        info["oauth_client_id_suffix"] = cid[-4:]
    try:
        wc = agent_workspace_client()
        me = wc.current_user.me()
        info["genie_workspace_client_user_name"] = getattr(me, "user_name", None) or getattr(
            me, "userName", None
        )
    except Exception as exc:
        info["genie_workspace_client_me_error"] = str(exc)[:400]
    return info