#!/usr/bin/env python3
"""Grant CAN_USE on a Databricks App to the current CLI user (or ``--user``).

Used after ``deploy_app.sh`` so the deploy identity can invoke the app (and so Genie Code’s
**Custom MCP server** picker can include apps the user is allowed to use).

Requires ``databricks-sdk`` and the same auth as ``databricks apps deploy`` (e.g. profile or
``databricks auth login``).

Example::

  export DATABRICKS_CONFIG_PROFILE=myprofile
  ./scripts/grant_deploy_user_app_can_use.py --app-name mcp-arango-agent
  ./scripts/grant_deploy_user_app_can_use.py --app-name mcp-arango-agent --user alice@example.com
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app-name", required=True, help="Databricks App name (e.g. mcp-arango-agent)")
    p.add_argument(
        "--user",
        default="",
        help="Workspace user name (email). Default: current_user.me() from the CLI session.",
    )
    args = p.parse_args()
    app_name = (args.app_name or "").strip()
    if not app_name:
        print("ERROR: --app-name is required", file=sys.stderr)
        return 2

    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.apps import AppAccessControlRequest, AppPermissionLevel
    except ImportError as exc:
        print(f"ERROR: databricks-sdk: {exc}", file=sys.stderr)
        return 1

    try:
        w = WorkspaceClient()
    except Exception as exc:
        print(f"ERROR: WorkspaceClient(): {exc}", file=sys.stderr)
        return 1

    user_name = (args.user or "").strip()
    if not user_name:
        try:
            me = w.current_user.me()
        except Exception as exc:
            print(f"ERROR: current_user.me(): {exc}", file=sys.stderr)
            return 1
        user_name = (getattr(me, "user_name", None) or getattr(me, "userName", None) or "").strip()
    if not user_name:
        print(
            "ERROR: Could not resolve user name; pass --user explicitly.",
            file=sys.stderr,
        )
        return 1

    try:
        w.apps.update_permissions(
            app_name,
            access_control_list=[
                AppAccessControlRequest(
                    user_name=user_name,
                    permission_level=AppPermissionLevel.CAN_USE,
                )
            ],
        )
    except Exception as exc:
        print(
            f"ERROR: apps.update_permissions({app_name!r}, user={user_name!r}): {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"OK: CAN_USE on app {app_name!r} for user {user_name!r} (PATCH app permissions).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
