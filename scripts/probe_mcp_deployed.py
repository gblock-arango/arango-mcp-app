#!/usr/bin/env python3
"""Step-by-step probes for a **deployed** mcp-arango-agent (Genie Code MCP troubleshooting).

Run from a machine with Databricks CLI auth (same profile as deploy). Example::

  export DATABRICKS_CONFIG_PROFILE=DEFAULT
  export APP_URL='https://….databricksapps.com'   # from deploy_app.sh DATABRICKS_APP_URL
  python3 scripts/probe_mcp_deployed.py --step 1
  python3 scripts/probe_mcp_deployed.py --step all

Steps map to common Genie Code "could not be added" causes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_APP_NAME = "mcp-arango-agent"


def _strip_bearer(value: str) -> str:
    v = (value or "").strip()
    if v.lower().startswith("bearer "):
        return v[7:].strip()
    return v


def resolve_bearer_token(
    *,
    env_token: str,
    prefer_sdk: bool,
    app_name: str,
    try_audience_exchange: bool,
) -> tuple[str, str]:
    """Return ``(token, source_label)`` for Databricks Apps HTTP auth.

    Apps expect OAuth U2M tokens (``databricks auth login`` / ``databricks auth token``),
    not arbitrary legacy PATs in ``DATABRICKS_TOKEN``. See
    https://docs.databricks.com/aws/en/dev-tools/databricks-apps/connect-local
    """
    candidates: list[tuple[str, str]] = []

    if prefer_sdk:
        try:
            from databricks.sdk import WorkspaceClient

            ws = WorkspaceClient()
            auth = ws.config.authenticate() or {}
            for key, val in auth.items():
                if str(key).lower() == "authorization" and val:
                    candidates.append((_strip_bearer(str(val)), "WorkspaceClient.config.authenticate()"))
                    break
        except Exception as exc:
            print(f"Note: SDK authenticate() failed: {exc}")

    if env_token:
        candidates.append((_strip_bearer(env_token), "DATABRICKS_TOKEN env"))

    try:
        proc = subprocess.run(
            ["databricks", "auth", "token", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            tok = _strip_bearer(str(data.get("access_token") or data.get("token") or ""))
            if tok:
                candidates.append((tok, "databricks auth token -o json"))
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"Note: databricks auth token failed: {exc}")

    if not candidates:
        return "", "none"

    # De-dupe while preserving order
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for tok, src in candidates:
        if tok and tok not in seen:
            seen.add(tok)
            unique.append((tok, src))

    if not try_audience_exchange or not app_name:
        return unique[0]

    host = (os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_WORKSPACE_ORIGIN") or "").strip()
    if not host:
        try:
            from databricks.sdk import WorkspaceClient

            host = str(WorkspaceClient().config.host or "").strip()
        except Exception:
            host = ""

    if not host:
        return unique[0]

    try:
        from databricks.sdk import WorkspaceClient

        ws = WorkspaceClient()
        app = ws.apps.get(app_name)
        audience = str(getattr(app, "oauth2_app_client_id", None) or "").strip()
    except Exception as exc:
        print(f"Note: could not load app oauth2_app_client_id for audience exchange: {exc}")
        return unique[0]

    if not audience:
        return unique[0]

    subject, subject_src = unique[0]
    exchanged = _exchange_token_for_app_audience(
        workspace_host=host,
        subject_token=subject,
        app_oauth_client_id=audience,
    )
    if exchanged:
        return exchanged, f"OIDC token exchange (audience={audience[:8]}…, subject from {subject_src})"
    return unique[0]


def _exchange_token_for_app_audience(
    *,
    workspace_host: str,
    subject_token: str,
    app_oauth_client_id: str,
) -> str:
    """Audience-scoped token for a specific app (notebook / app-to-app pattern)."""
    url = f"{workspace_host.rstrip('/')}/oidc/v1/token"
    body = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": subject_token,
            "subject_token_type": "urn:databricks:params:oauth:token-type:access_token",
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "scope": "all-apis",
            "audience": app_oauth_client_id,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:400]
        print(f"Note: audience token exchange HTTP {e.code}: {err_body}")
        return ""
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"Note: audience token exchange failed: {exc}")
        return ""
    return _strip_bearer(str(data.get("access_token") or ""))


def _print_exception_chain(exc: BaseException, *, limit: int = 8) -> None:
    """Unwrap ExceptionGroup / TaskGroup so HTTP 401/403 bodies are visible."""
    import traceback

    print("--- underlying errors ---")
    shown = 0

    def _walk(e: BaseException) -> None:
        nonlocal shown
        if shown >= limit:
            return
        subs = getattr(e, "exceptions", None)
        if subs:
            for sub in subs:
                _walk(sub)
            return
        shown += 1
        print(f"  [{shown}] {type(e).__name__}: {e}")
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                body = (getattr(resp, "text", None) or "")[:800]
            except Exception:
                body = ""
            print(f"       HTTP {getattr(resp, 'status_code', '?')}")
            if body:
                print(f"       body: {body}")

    _walk(exc)
    if shown == 0:
        traceback.print_exception(type(exc), exc, exc.__traceback__, limit=6)


def _get(url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None, timeout: float = 60.0) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body


def _warn_if_not_mcp_agent_app(base: str) -> None:
    host = base.split("//", 1)[-1].split("/", 1)[0].lower()
    if "mcp-" not in host and "mcp_arango" not in host:
        print(
            "WARNING: APP_URL host does not look like the Genie MCP app "
            "(expected name starting with mcp-, e.g. mcp-arango-agent-….databricksapps.com). "
            f"Got host: {host}"
        )


def step1_health(base: str, token: str) -> bool:
    print("=== Step 1: App health ===")
    _warn_if_not_mcp_agent_app(base)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    code, body = _get(f"{base.rstrip('/')}/api/health", headers=headers)
    print(f"GET /api/health -> {code}")
    print(body[:500])
    ok = code == 200 and "ok" in body.lower()
    print("PASS" if ok else "FAIL — app not reachable")
    return ok


def step2_diagnostics(base: str, token: str) -> bool:
    print("\n=== Step 2: MCP diagnostics (tool counts) ===")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    code, body = _get(f"{base.rstrip('/')}/api/mcp/diagnostics", headers=headers)
    print(f"GET /api/mcp/diagnostics -> {code}")
    if code != 200:
        print(body[:800])
        print("FAIL — set DATABRICKS_TOKEN or use app URL with user auth")
        return False
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        print("FAIL — not JSON")
        return False
    print(json.dumps(d, indent=2)[:4000])
    gc = d.get("genie_code_mcp") or {}
    n = gc.get("tool_count", 0)
    ok = n > 0 and n <= 20
    print(f"Genie Code tools: {n} (expect 5, must be ≤20 combined with other MCP servers)")
    print("PASS" if ok else "FAIL")
    return ok


def step3_mcp_protocol(base: str, token: str) -> bool:
    print("\n=== Step 3: MCP initialize + tools/list on /mcp (Genie Code path) ===")
    _warn_if_not_mcp_agent_app(base)
    if not token:
        print("FAIL — set DATABRICKS_TOKEN (Databricks Apps require user auth for /mcp)")
        return False
    try:
        import anyio
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        print(f"SKIP — install mcp package: {exc}")
        return False

    url = f"{base.rstrip('/')}/mcp/"

    async def _run() -> list[str]:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(60.0),
        ) as http_client:
            async with (
                streamable_http_client(url, http_client=http_client) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                listed = await session.list_tools()
        return [t.name for t in listed.tools]

    try:
        names = anyio.run(_run)
    except Exception as exc:
        print(f"FAIL — MCP client error (this is what Genie Code likely hits): {exc}")
        _print_exception_chain(exc)
        if "401" in str(exc):
            print(
                "Hint: 401 with CAN_USE already granted usually means the Bearer token is wrong:\n"
                "  • Use U2M: databricks auth login --host https://dbc-….cloud.databricks.com\n"
                "  • Then: export DATABRICKS_TOKEN=$(databricks auth token -o json | python3 -c "
                "\"import json,sys; print(json.load(sys.stdin)['access_token'])\")\n"
                "  • Or re-run with: python3 scripts/probe_mcp_deployed.py --step 3 --sdk-auth\n"
                "  • Legacy PATs often fail on *.databricksapps.com — see\n"
                "    https://docs.databricks.com/aws/en/dev-tools/databricks-apps/connect-local\n"
                "Genie Code in the browser uses your workspace session (not DATABRICKS_TOKEN); "
                "if Step 3 passes but Genie still fails, run --step 4 (CORS)."
            )
        return False
    print(f"tools: {names}")
    ok = len(names) == 5
    print("PASS" if ok else "FAIL")
    return ok


def step4_cors_preflight(base: str, workspace_origin: str) -> bool:
    print("\n=== Step 4: CORS preflight (browser / Genie Code) ===")
    if not workspace_origin:
        print("SKIP — pass --workspace-origin https://dbc-….cloud.databricks.com")
        return True
    import httpx

    mcp_url = f"{base.rstrip('/')}/mcp/"
    try:
        r = httpx.options(
            mcp_url,
            headers={
                "Origin": workspace_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
            timeout=30.0,
        )
    except Exception as exc:
        print(f"FAIL — {exc}")
        return False
    print(f"OPTIONS {mcp_url} -> {r.status_code}")
    acao = r.headers.get("access-control-allow-origin", "")
    print(f"Access-Control-Allow-Origin: {acao!r}")
    ok = r.status_code in (200, 204) and (acao == workspace_origin or acao == "*")
    print("PASS" if ok else "FAIL — set MCP_CORS_ALLOW_ORIGINS or rely on DATABRICKS_HOST auto-CORS; redeploy")
    return ok


def step5_llm_chat(base: str, token: str) -> bool:
    print("\n=== Step 5: Dashboard MCP chat (LLM + internal tools) ===")
    if not token:
        print("SKIP — set DATABRICKS_TOKEN for POST /api/genie-mcp/chat")
        return True
    code, body = _post_json(
        f"{base.rstrip('/')}/api/genie-mcp/chat",
        {"content": "Reply with exactly: pong"},
        headers={"Authorization": f"Bearer {token}"},
    )
    print(f"POST /api/genie-mcp/chat -> {code}")
    print(body[:1200])
    if code != 200:
        print("FAIL")
        return False
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        return False
    ok = d.get("ok") is True
    print("PASS" if ok else f"FAIL — {d.get('error')}")
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app-url", default=os.environ.get("APP_URL", "").strip())
    p.add_argument("--token", default=os.environ.get("DATABRICKS_TOKEN", "").strip())
    p.add_argument("--app-name", default=os.environ.get("DATABRICKS_APP_NAME", DEFAULT_APP_NAME).strip())
    p.add_argument(
        "--sdk-auth",
        action="store_true",
        default=True,
        help="Prefer WorkspaceClient / databricks auth token over raw DATABRICKS_TOKEN (default: on)",
    )
    p.add_argument(
        "--no-sdk-auth",
        action="store_false",
        dest="sdk_auth",
        help="Use only --token / DATABRICKS_TOKEN",
    )
    p.add_argument(
        "--audience-exchange",
        action="store_true",
        default=True,
        help="If SDK/U2M token fails, try OIDC token exchange scoped to the app (default: on)",
    )
    p.add_argument(
        "--no-audience-exchange",
        action="store_false",
        dest="audience_exchange",
    )
    p.add_argument(
        "--workspace-origin",
        default=os.environ.get("DATABRICKS_WORKSPACE_ORIGIN", "").strip(),
        help="e.g. https://dbc-xxxx.cloud.databricks.com (no path)",
    )
    p.add_argument("--step", default="all", help="1|2|3|4|5|all")
    args = p.parse_args()
    if not args.app_url:
        print("ERROR: set APP_URL or --app-url to the deployed mcp-arango-agent URL", file=sys.stderr)
        return 2

    token, token_src = resolve_bearer_token(
        env_token=args.token,
        prefer_sdk=args.sdk_auth,
        app_name=args.app_name,
        try_audience_exchange=args.audience_exchange,
    )
    if token:
        print(f"Using Bearer token from: {token_src}")
    else:
        print("WARNING: no Bearer token resolved — steps 1–3 will likely 401", file=sys.stderr)

    steps = {
        "1": lambda: step1_health(args.app_url, token),
        "2": lambda: step2_diagnostics(args.app_url, token),
        "3": lambda: step3_mcp_protocol(args.app_url, token),
        "4": lambda: step4_cors_preflight(args.app_url, args.workspace_origin),
        "5": lambda: step5_llm_chat(args.app_url, token),
    }
    if args.step == "all":
        results = [fn() for fn in steps.values()]
    elif args.step in steps:
        results = [steps[args.step]()]
    else:
        print(f"Unknown step {args.step!r}; use 1-5 or all", file=sys.stderr)
        return 2

    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
