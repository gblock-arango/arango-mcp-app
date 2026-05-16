"""MCP mode (dashboard label **MCP**): workspace foundation-model chat with in-process MCP (Arango) tool calling.

This is **not** Databricks Genie Code; it runs LLM + tools inside arango-mcp-app using OpenAI-compatible
chat on the workspace ``/serving-endpoints`` URL and this app's registered FastMCP tools (gateway-backed
Arango when configured). Dashboard **MCP** mode uses the **full** tool catalog on ``/mcp/internal``
(:class:`arango_mcp.server.mcp_app`). Genie Code uses the small surface at ``/mcp`` only.

Requires ``openai``, ``TOOL_ROUTER_SERVING_ENDPOINT`` or ``GENIEMCP_SERVING_ENDPOINT`` (or
``GENIEMCP_FOUNDATION_MODEL_QUERY`` to resolve a READY endpoint via the SDK), workspace auth
(Databricks App runtime), and a reachable Arango path (gateway or direct) so tools can run.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from typing import Any, Mapping

import anyio
from openai import OpenAI

from arango_dashboard_agent.services.async_bridge import run_on_main_loop
from arango_dashboard_agent.services.genie_workspace_client import agent_workspace_client
from arango_mcp.arango_connector import arango_connector
from arango_mcp.server import mcp_app

logger = logging.getLogger(__name__)

# Databricks foundation-model ``chat.completions`` (e.g. Meta Llama 3.3) rejects >32 tools.
SERVING_API_TOOLS_HARD_CAP = 32

_SYSTEM = (
    "You are an expert assistant with access to ArangoDB MCP tools. "
    "Use tools to answer the user. Prefer read/query tools before writes. "
    "Give a concise final answer in natural language after using tools."
)


def _workspace_openai_client() -> OpenAI:
    ws = agent_workspace_client()
    host = (ws.config.host or "").strip().rstrip("/")
    if not host:
        raise ValueError("Workspace host is empty (configure DATABRICKS_HOST / SDK).")
    auth = ws.config.authenticate() or {}
    lower = {str(k).lower(): v for k, v in auth.items()}
    bearer = str(lower.get("authorization") or "").strip()
    if bearer.lower().startswith("bearer "):
        token = bearer[7:].strip()
    else:
        token = str(lower.get("token") or "").strip()
    if not token:
        raise ValueError(
            "Could not obtain a bearer token from WorkspaceClient.config.authenticate(); "
            "MCP mode needs workspace OAuth (Databricks App runtime)."
        )
    return OpenAI(api_key=token, base_url=f"{host}/serving-endpoints")


def _sanitize_tool_parameters_for_serving(schema: Any) -> dict[str, Any]:
    """
    Databricks foundation-model chat requires JSON Schema with ``additionalProperties: false``
    (or omitted). Pydantic/FastMCP often emits ``additionalProperties: true`` on open dicts.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "additionalProperties": False}

    out = copy.deepcopy(schema)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if "additionalProperties" in node and node["additionalProperties"] is not False:
                node["additionalProperties"] = False
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(out)
    if out.get("type") == "object" and "additionalProperties" not in out:
        out["additionalProperties"] = False
    return out


def _prioritize_tools_for_model(
    raw: list[Any],
    *,
    user_text: str,
    cap: int,
) -> list[Any]:
    """Choose up to ``cap`` tools from the full catalog (model API limit, not MCP registration)."""
    if len(raw) <= cap:
        return raw
    words = [w.lower() for w in user_text.split() if len(w) > 2]
    read_prefixes = ("list-", "get-", "execute-aql", "read-", "describe-", "show-")

    def _score(tool: Any) -> int:
        name = str(getattr(tool, "name", "") or "").lower()
        desc = str(getattr(tool, "description", "") or "").lower()[:800]
        score = 0
        for w in words:
            if w in name:
                score += 12
            if w in desc:
                score += 3
        if any(name.startswith(p) for p in read_prefixes):
            score += 2
        return score

    ranked = sorted(raw, key=_score, reverse=True)
    return ranked[:cap]


def _tools_for_openai(*, max_tools: int, user_text: str = "") -> list[dict[str, Any]]:
    """Subset of ``mcp_app`` tools for one serving-endpoint request (full catalog still callable)."""
    cap = max(1, min(int(max_tools), SERVING_API_TOOLS_HARD_CAP))
    all_tools = list(mcp_app._tool_manager.list_tools())
    selected = _prioritize_tools_for_model(all_tools, user_text=user_text, cap=cap)
    if len(all_tools) > cap:
        logger.info(
            "MCP orchestrator: offering %s of %s tools to model (API cap %s); full catalog on /mcp/internal",
            len(selected),
            len(all_tools),
            SERVING_API_TOOLS_HARD_CAP,
        )
    out: list[dict[str, Any]] = []
    for t in selected:
        desc = (t.description or t.name)[:4000]
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": desc,
                    "parameters": _sanitize_tool_parameters_for_serving(
                        t.parameters or {"type": "object", "properties": {}}
                    ),
                },
            }
        )
    return out


def _tool_result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)[:120000]
    except TypeError:
        return str(result)[:120000]


async def ask_genie_mcp_conversation(
    *,
    content: str,
    conversation_id: str | None,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        return {"ok": False, "error": "content is empty"}

    router_ep = str(config.get("TOOL_ROUTER_SERVING_ENDPOINT") or "").strip()
    genie_ep = str(config.get("GENIEMCP_SERVING_ENDPOINT") or "").strip()
    fm_query = str(config.get("GENIEMCP_FOUNDATION_MODEL_QUERY") or "").strip()
    deep = bool(config.get("GENIEMCP_RESOLVE_FOUNDATION_ENDPOINT_DEEP"))

    endpoint = router_ep or genie_ep
    if not endpoint and fm_query and not router_ep:
        try:
            from arango_dashboard_agent.services.foundation_model_endpoint_resolver import (
                resolve_serving_endpoint_name,
            )

            def _resolve() -> str | None:
                return resolve_serving_endpoint_name(
                    agent_workspace_client(),
                    fm_query,
                    deep=deep,
                    require_ready=True,
                )

            resolved = await anyio.to_thread.run_sync(_resolve)
        except Exception as exc:
            logger.warning("MCP mode: foundation model endpoint resolve failed: %s", exc)
            resolved = None
        if resolved:
            endpoint = resolved
            logger.info(
                "MCP mode: GENIEMCP_FOUNDATION_MODEL_QUERY %r resolved to serving endpoint %r",
                fm_query,
                resolved,
            )

    if not endpoint:
        return {
            "ok": False,
            "error": (
                "No serving endpoint configured. Set TOOL_ROUTER_SERVING_ENDPOINT (preferred for "
                "dashboard / ToolRouter) and/or GENIEMCP_SERVING_ENDPOINT, or set "
                "GENIEMCP_FOUNDATION_MODEL_QUERY so the app can resolve a READY foundation-model endpoint "
                "via the Databricks SDK (optional GENIEMCP_RESOLVE_FOUNDATION_ENDPOINT_DEEP=true)."
            ),
        }

    max_rounds = int(config.get("GENIEMCP_MAX_ROUNDS") or 8)
    max_rounds = max(1, min(max_rounds, 24))

    outbound = str(config.get("OUTBOUND_BEARER_TOKEN") or "").strip() or None
    gw_cfg = {
        "ARANGO_GATEWAY_BASE_URL": str(config.get("ARANGO_GATEWAY_BASE_URL") or ""),
        "ARANGO_GATEWAY_REGISTRY_TABLE": str(config.get("ARANGO_GATEWAY_REGISTRY_TABLE") or ""),
        "DATABRICKS_SQL_WAREHOUSE_ID": str(config.get("DATABRICKS_SQL_WAREHOUSE_ID") or ""),
        "OUTBOUND_BEARER_TOKEN": outbound or "",
    }
    try:
        await arango_connector.connect(
            gateway_auth_config=gw_cfg,
            outbound_bearer=outbound,
        )
    except Exception as exc:
        logger.warning("MCP mode: Arango connector connect failed: %s", exc)
        return {"ok": False, "error": f"Arango/gateway not reachable for MCP tools: {exc}"}

    try:
        client = _workspace_openai_client()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    model_max_tools = int(
        config.get("GENIEMCP_MODEL_MAX_TOOLS")
        or config.get("GENIEMCP_MAX_TOOLS")
        or SERVING_API_TOOLS_HARD_CAP
    )
    tools = _tools_for_openai(max_tools=model_max_tools, user_text=text)
    if not tools:
        return {"ok": False, "error": "No MCP tools are registered on this server."}
    tools_registered = len(mcp_app._tool_manager.list_tools())

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": text},
    ]

    cid = (conversation_id or "").strip() or str(uuid.uuid4())
    rounds = 0
    tools_invoked: list[str] = []

    def _chat_once() -> Any:
        return client.chat.completions.create(
            model=endpoint,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.2,
        )

    while rounds < max_rounds:
        rounds += 1
        try:
            resp = await anyio.to_thread.run_sync(_chat_once)
        except Exception as exc:
            logger.warning("MCP mode: model request failed: %s", exc)
            return {"ok": False, "error": f"Model request failed: {exc}"}

        choice = resp.choices[0].message
        tool_calls = choice.tool_calls
        if not tool_calls:
            out = (choice.content or "").strip() or "(no text reply)"
            return {
                "ok": True,
                "conversation_id": cid,
                "message_id": str(getattr(resp, "id", "") or "") or None,
                "message": {"content": out, "status": "SUCCEEDED"},
                "tools_invoked": tools_invoked,
                "model_rounds": rounds,
                "tools_offered_to_model": len(tools),
                "tools_registered": tools_registered,
            }

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": choice.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in tool_calls
            ],
        }
        messages.append(assistant_msg)

        for tc in tool_calls:
            name = tc.function.name
            tools_invoked.append(name)
            raw_args = tc.function.arguments or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            try:
                out = await mcp_app._tool_manager.call_tool(
                    name, args, context=None, convert_result=True
                )
                payload = _tool_result_text(out)
            except Exception as exc:
                payload = f"tool error: {exc}"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": payload,
                }
            )

    return {
        "ok": False,
        "error": f"MCP mode exceeded maximum tool rounds ({max_rounds}).",
        "tools_invoked": tools_invoked,
        "model_rounds": rounds,
    }


def ask_genie_mcp_conversation_sync(
    *,
    content: str,
    conversation_id: str | None,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Run :func:`ask_genie_mcp_conversation` from sync Flask (Gunicorn worker)."""
    timeout = float(config.get("GENIEMCP_SYNC_TIMEOUT_SECONDS") or 660.0)
    try:
        return run_on_main_loop(
            ask_genie_mcp_conversation(
                content=content,
                conversation_id=conversation_id,
                config=config,
            ),
            timeout=timeout,
        )
    except Exception as exc:
        logger.exception("MCP orchestration failed in sync bridge")
        return {"ok": False, "error": f"MCP orchestration failed: {exc}"}
