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
