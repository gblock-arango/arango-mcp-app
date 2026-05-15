"""JSON manifests under this package describe MCP tool registries (documentation + CI hints)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def manifest_path(stem: str) -> Path:
    """Return ``{stem}_manifest.json`` in this directory (e.g. stem ``genie_code``)."""
    return Path(__file__).resolve().parent / f"{stem}_manifest.json"


def load_manifest(stem: str) -> dict[str, Any]:
    with open(manifest_path(stem), encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data


def genie_code_allowed_tool_names(*, max_tools: int = 20) -> list[str]:
    """Tool names from ``genie_code_manifest.json``, capped for Genie Code ``/mcp`` only."""
    manifest = load_manifest("genie_code")
    names = [str(n) for n in (manifest.get("tool_names") or [])]
    cap = max(1, min(int(max_tools), 40))
    return names[:cap]
