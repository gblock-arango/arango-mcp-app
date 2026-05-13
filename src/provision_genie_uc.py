#!/usr/bin/env python3
"""CLI: create Genie space if missing and upsert id into GENIE_SPACE_REGISTRY_TABLE."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    src = Path(__file__).resolve().parent
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from arango_agent.services.genie_registry import provision_genie_space_cli

    return provision_genie_space_cli()


if __name__ == "__main__":
    raise SystemExit(main())
