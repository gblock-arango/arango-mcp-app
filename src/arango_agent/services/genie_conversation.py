"""
Prototype client for the Databricks Genie Conversation API.

Uses :class:`databricks.sdk.WorkspaceClient` (same auth model as the arango-agent HTTP app:
Databricks Apps inject workspace identity / PAT as configured).

REST flow (see Databricks docs — Genie conversation API):
  POST /api/2.0/genie/spaces/{space_id}/start-conversation
  POST /api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages
  GET  .../messages/{message_id} (polling; SDK helpers below wrap this)

This module is intentionally small: a later multi-agent App can replace it while
keeping the same HTTP boundary on this Flask service if desired.

Space id resolution from Unity Catalog lives in :mod:`genie_registry` (optional).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError
from databricks.sdk.service.dashboards import GenieMessage

logger = logging.getLogger(__name__)


def genie_message_to_dict(message: GenieMessage) -> dict[str, Any]:
    """Serialize a :class:`GenieMessage` for JSON responses (uses SDK ``as_dict()``)."""
    return message.as_dict()


def ask_genie_conversation(
    *,
    workspace_client: WorkspaceClient,
    space_id: str,
    content: str,
    conversation_id: str | None = None,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    """
    Ask a question in a Genie Space, optionally continuing an existing conversation.

    When ``conversation_id`` is None, starts a new thread (stateful follow-ups use the
    returned ``conversation_id`` on the next call).

    ``timeout_seconds`` bounds SDK-side waiting for Genie to reach a terminal message
    state (default 10 minutes).

    Returns a dict with ``ok: bool`` and either Genie fields or an ``error`` string.
    """
    space_id = (space_id or "").strip()
    content = (content or "").strip()
    if not space_id:
        return {"ok": False, "error": "space_id is empty"}
    if not content:
        return {"ok": False, "error": "content is empty"}

    timeout = timedelta(seconds=max(1.0, float(timeout_seconds)))
    genie = workspace_client.genie

    try:
        if conversation_id:
            cid = conversation_id.strip()
            if not cid:
                return {"ok": False, "error": "conversation_id is empty"}
            message = genie.create_message_and_wait(
                space_id=space_id,
                conversation_id=cid,
                content=content,
                timeout=timeout,
            )
        else:
            message = genie.start_conversation_and_wait(
                space_id=space_id,
                content=content,
                timeout=timeout,
            )
    except DatabricksError as exc:
        logger.warning("Genie Conversation API error: %s", exc)
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error calling Genie")
        return {"ok": False, "error": str(exc)}

    mid = (message.message_id or message.id or "").strip()
    cid = (message.conversation_id or "").strip()
    return {
        "ok": True,
        "space_id": space_id,
        "conversation_id": cid,
        "message_id": mid,
        "status": str(message.status) if message.status is not None else None,
        "message": genie_message_to_dict(message),
    }
