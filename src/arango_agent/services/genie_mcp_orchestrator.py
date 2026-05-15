"""MCP mode (dashboard label **MCP**): workspace foundation-model chat with in-process MCP (Arango) tool calling.

This is **not** Databricks Genie Code; it runs LLM + tools inside arango-agent using OpenAI-compatible
chat on the workspace ``/serving-endpoints`` URL and this app's registered FastMCP tools (gateway-backed
Arango when configured). Dashboard **MCP** mode uses the **full** tool catalog on ``/mcp/internal``
(:class:`arango_mcp.server.mcp_app`). Genie Code uses the small surface at ``/mcp`` only.

Requires ``openai``, ``TOOL_ROUTER_SERVING_ENDPOINT`` or ``GENIEMCP_SERVING_ENDPOINT`` (or
``GENIEMCP_FOUNDATION_MODEL_QUERY`` to resolve a READY endpoint via the SDK), workspace auth
(Databricks App runtime), and a reachable Arango path (gateway or direct) so tools can run.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Mapping

import anyio
from openai import OpenAI

from arango_agent.services.genie_workspace_client import agent_workspace_client
from arango_mcp.arango_connector import arango_connector
from arango_mcp.server import mcp_app

logger = logging.getLogger(__name__)

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


def _tools_for_openai(max_tools: int) -> list[dict[str, Any]]:
    raw = mcp_app._tool_manager.list_tools()[:max_tools]
    tools: list[dict[str, Any]] = []
    for t in raw:
        desc = (t.description or t.name)[:4000]
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": desc,
                    "parameters": t.parameters or {"type": "object", "properties": {}},
                },
            }
        )
    return tools


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
            from arango_agent.services.foundation_model_endpoint_resolver import (
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

    max_tools = int(config.get("GENIEMCP_MAX_TOOLS") or 20)
    max_rounds = int(config.get("GENIEMCP_MAX_ROUNDS") or 8)
    max_tools = max(1, min(max_tools, 40))
    max_rounds = max(1, min(max_rounds, 24))

    try:
        await arango_connector.connect()
    except Exception as exc:
        logger.warning("MCP mode: Arango connector connect failed: %s", exc)
        return {"ok": False, "error": f"Arango/gateway not reachable for MCP tools: {exc}"}

    try:
        client = _workspace_openai_client()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    tools = _tools_for_openai(max_tools)
    if not tools:
        return {"ok": False, "error": "No MCP tools are registered on this server."}

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": text},
    ]

    cid = (conversation_id or "").strip() or str(uuid.uuid4())
    rounds = 0

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
    }


def ask_genie_mcp_conversation_sync(
    *,
    content: str,
    conversation_id: str | None,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Run :func:`ask_genie_mcp_conversation` from sync Flask (Gunicorn worker)."""
    return anyio.run(
        ask_genie_mcp_conversation,
        content=content,
        conversation_id=conversation_id,
        config=config,
    )
