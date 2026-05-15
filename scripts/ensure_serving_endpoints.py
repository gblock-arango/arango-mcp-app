#!/usr/bin/env python3
"""Summarize model serving endpoint readiness for dashboard MCP (GENIEMCP / TOOL_ROUTER).

Uses :class:`databricks.sdk.WorkspaceClient` (same auth as the Databricks CLI when
``DATABRICKS_CONFIG_PROFILE`` matches ``deploy_app.sh``'s profile argument).

Pay-per-token foundation models are **pre-provisioned** per workspace; this script only **reads**
endpoint metadata (``serving_endpoints.get``). It does **not** create endpoints (that path is
billing-sensitive and usually done in the Serving UI or via explicit PT APIs).

Unity Catalog does not expose "this FM endpoint is READY"; use the Serving API / this script.
"""

from __future__ import annotations

import os
import sys


def _entity_hint(se) -> str:
    if se is None:
        return ""
    fm = getattr(se, "foundation_model", None)
    if fm is not None:
        name = getattr(fm, "name", None)
        if name:
            return f"foundation_model={name!r}"
    en = getattr(se, "entity_name", None) or ""
    ev = getattr(se, "entity_version", None) or ""
    if en:
        return f"entity_name={en!r}" + (f"@{ev!r}" if ev else "")
    em = getattr(se, "external_model", None)
    if em is not None:
        return f"external_model={getattr(em, 'name', em)!r}"
    return ""


def main() -> int:
    pairs: list[tuple[str, str]] = []
    for env_key in ("GENIEMCP_SERVING_ENDPOINT", "TOOL_ROUTER_SERVING_ENDPOINT"):
        v = (os.environ.get(env_key) or "").strip()
        if v:
            pairs.append((env_key, v))

    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for env_key, name in pairs:
        if name not in seen:
            seen.add(name)
            uniq.append((env_key, name))

    if not uniq:
        print(
            "ensure_serving_endpoints: no GENIEMCP_SERVING_ENDPOINT or "
            "TOOL_ROUTER_SERVING_ENDPOINT set — skip."
        )
        return 0

    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.errors import NotFound, ResourceDoesNotExist
    except ImportError as exc:
        print(f"ensure_serving_endpoints: databricks-sdk not importable: {exc}", file=sys.stderr)
        return 0

    try:
        w = WorkspaceClient()
    except Exception as exc:
        print(f"ensure_serving_endpoints: WorkspaceClient() failed: {exc}", file=sys.stderr)
        return 0

    print("ensure_serving_endpoints: WorkspaceClient serving-endpoints summary")
    exit_status = 0
    for env_key, name in uniq:
        print(f"  [{env_key}] -> {name!r}")
        try:
            ep = w.serving_endpoints.get(name)
        except (NotFound, ResourceDoesNotExist):
            print(
                "    NOT FOUND. For pay-per-token FMs, open **Serving** in the workspace and copy "
                "the exact endpoint name from the Foundation Model APIs list. "
                "For custom / PT endpoints, create them in UI or PT API first."
            )
            exit_status = 1
            continue
        except Exception as exc:
            print(f"    ERROR: {exc}")
            exit_status = 1
            continue

        st = ep.state
        ready = getattr(st, "ready", None) if st else None
        ready_v = getattr(ready, "value", ready)
        cu = getattr(st, "config_update", None) if st else None
        cu_v = getattr(cu, "value", cu)
        print(f"    state.ready={ready_v!r} state.config_update={cu_v!r}")

        cfg = ep.config
        entities = (cfg.served_entities or []) if cfg else []
        if entities:
            for i, se in enumerate(entities[:3]):
                hint = _entity_hint(se)
                print(f"    served_entities[{i}]: {hint or repr(se)}")
            if len(entities) > 3:
                print(f"    … and {len(entities) - 3} more served_entities")

        if ready_v and str(ready_v).upper() != "READY":
            exit_status = 1

    if exit_status != 0:
        print(
            "ensure_serving_endpoints: one or more endpoints missing or not READY "
            "(dashboard MCP chat will fail until fixed).",
            file=sys.stderr,
        )
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
