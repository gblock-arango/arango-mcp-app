"""Resolve pay-per-token (or other) **serving endpoint names** from a foundation-model query string.

Databricks exposes workspace **serving endpoint** names (for example ``databricks-gpt-5-mini``) that
map to a ``foundation_model`` record on each served entity. There is no separate public
"register this string as GENIEMCP_SERVING_ENDPOINT" API: you **discover** the endpoint name via
``WorkspaceClient.serving_endpoints.list`` (and optionally ``get`` when list summaries omit FM
metadata).

See also: https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/supported-models
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)


def _ready_ok(state: Any) -> bool:
    if state is None:
        return False
    r = getattr(state, "ready", None)
    if r is None:
        return False
    v = getattr(r, "value", r)
    return str(v).upper() == "READY"


def _foundation_model_names_from_served_entities(served_entities: list[Any] | None) -> list[str]:
    out: list[str] = []
    if not served_entities:
        return out
    for se in served_entities:
        fm = getattr(se, "foundation_model", None)
        if fm is None:
            continue
        n = getattr(fm, "name", None)
        if n:
            out.append(str(n))
    return out


def _names_for_list_item(ep: Any) -> list[str]:
    cfg = getattr(ep, "config", None)
    if cfg is None:
        return []
    entities = getattr(cfg, "served_entities", None)
    return _foundation_model_names_from_served_entities(entities)


def _collect_fm_endpoints(w: WorkspaceClient, *, deep: bool) -> list[tuple[str, list[str], bool]]:
    """Return list of (endpoint_name, foundation_model_names, ready)."""
    rows: list[tuple[str, list[str], bool]] = []
    for ep in w.serving_endpoints.list():
        name = (getattr(ep, "name", None) or "").strip()
        if not name:
            continue
        ready = _ready_ok(getattr(ep, "state", None))
        fm_names = _names_for_list_item(ep)
        if not fm_names and deep:
            try:
                detail = w.serving_endpoints.get(name)
            except Exception as exc:
                logger.debug("resolve_fm_endpoint: get(%r) failed: %s", name, exc)
            else:
                cfg = getattr(detail, "config", None)
                entities = getattr(cfg, "served_entities", None) if cfg else None
                fm_names = _foundation_model_names_from_served_entities(entities)
                ready = _ready_ok(getattr(detail, "state", None))
        rows.append((name, fm_names, ready))
    return rows



def _norm(s: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in s.strip()).strip("-")


def _score_match(query: str, endpoint_name: str, fm_names: list[str]) -> int:
    """Higher is better; 0 means no match."""
    q = query.strip()
    if not q:
        return 0
    ql = q.lower()
    en = endpoint_name.lower()

    if en == ql:
        return 1000
    for fm in fm_names:
        if fm.lower() == ql:
            return 900

    qn = _norm(q)
    en_slug = _norm(endpoint_name)
    if qn and qn == en_slug:
        return 850
    if qn and (qn in en_slug or en_slug in qn):
        return 400

    for fm in fm_names:
        fmn = _norm(fm)
        if qn and (qn in fmn or fmn in qn or ql in fm.lower() or fm.lower() in ql):
            return 300

    if ql in en or en in ql:
        return 200
    return 0


def resolve_serving_endpoint_name(
    w: WorkspaceClient,
    model_query: str,
    *,
    deep: bool = False,
    require_ready: bool = True,
) -> str | None:
    """
    Find a serving **endpoint name** whose foundation model or endpoint label matches ``model_query``.

    :param deep: If True, call ``serving_endpoints.get`` when list() omits ``foundation_model`` names.
    :param require_ready: If True, only consider endpoints whose state is READY.
    :returns: Endpoint name to pass as ``GENIEMCP_SERVING_ENDPOINT`` / OpenAI ``model``, or None.
    """
    q = (model_query or "").strip()
    if not q:
        return None

    rows = _collect_fm_endpoints(w, deep=deep)
    scored: list[tuple[int, bool, str, list[str]]] = []
    for endpoint_name, fm_names, ready in rows:
        if require_ready and not ready:
            continue
        sc = _score_match(q, endpoint_name, fm_names)
        if sc > 0:
            scored.append((sc, ready, endpoint_name, fm_names))

    if not scored:
        if require_ready:
            # Retry once including NOT_READY so the caller can surface a useful name.
            for endpoint_name, fm_names, ready in rows:
                sc = _score_match(q, endpoint_name, fm_names)
                if sc > 0:
                    scored.append((sc, ready, endpoint_name, fm_names))
        if not scored:
            return None

    scored.sort(key=lambda t: (-t[0], -int(t[1]), t[2]))
    best = scored[0]
    if len(scored) > 1 and scored[1][0] == best[0]:
        names = [x[2] for x in scored if x[0] == best[0]]
        logger.warning(
            "resolve_serving_endpoint_name: ambiguous query %r — picking %r among %s",
            q,
            best[2],
            names[:8],
        )
    return best[2]
