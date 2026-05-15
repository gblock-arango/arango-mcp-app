#!/usr/bin/env python3
"""Print ``GENIEMCP_SERVING_ENDPOINT`` for a foundation-model query using the Databricks SDK.

Examples::

  export DATABRICKS_CONFIG_PROFILE=myprofile
  ./scripts/resolve_serving_endpoint_for_foundation_model.py --model databricks-gpt-5-mini
  ./scripts/resolve_serving_endpoint_for_foundation_model.py --model "meta-llama/Meta-Llama-3.3-70B-Instruct" --deep

``--deep`` calls ``serving_endpoints.get`` for each workspace endpoint when list() summaries omit
``foundation_model`` metadata (slower; use when the default pass finds nothing).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_AGENT_SRC = Path(__file__).resolve().parents[1] / "src"
if _AGENT_SRC.is_dir():
    sys.path.insert(0, str(_AGENT_SRC))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--model",
        default=(os.environ.get("GENIEMCP_FOUNDATION_MODEL_QUERY") or "").strip(),
        help="Endpoint name, foundation_model.name substring, or catalog-style id (env: GENIEMCP_FOUNDATION_MODEL_QUERY)",
    )
    p.add_argument(
        "--deep",
        action="store_true",
        help="Call serving_endpoints.get for each endpoint when list() lacks foundation_model names",
    )
    p.add_argument(
        "--include-not-ready",
        action="store_true",
        help="Allow NOT_READY endpoints (default: READY only)",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON {\"endpoint\": ...} only")
    args = p.parse_args()
    if not args.model:
        p.error("pass --model or set GENIEMCP_FOUNDATION_MODEL_QUERY")

    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:
        print(f"ERROR: databricks-sdk not available: {exc}", file=sys.stderr)
        return 1

    try:
        w = WorkspaceClient()
    except Exception as exc:
        print(f"ERROR: WorkspaceClient() failed: {exc}", file=sys.stderr)
        return 1

    from arango_agent.services.foundation_model_endpoint_resolver import resolve_serving_endpoint_name

    ep = resolve_serving_endpoint_name(
        w,
        args.model,
        deep=args.deep,
        require_ready=not args.include_not_ready,
    )
    if not ep:
        print(
            f"No READY serving endpoint matched {args.model!r}. Try --deep or --include-not-ready.",
            file=sys.stderr,
        )
        return 2
    if args.json:
        print(json.dumps({"GENIEMCP_SERVING_ENDPOINT": ep}))
    else:
        print(ep)
        print(f"\nexport GENIEMCP_SERVING_ENDPOINT={ep!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
