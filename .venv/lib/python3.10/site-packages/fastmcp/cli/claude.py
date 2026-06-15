"""Claude app integration utilities."""

import json
import sys
from pathlib import Path
from typing import Optional

from ..utilities.logging import get_logger

logger = get_logger(__name__)


def get_claude_config_path() -> Path | None:
    """Get the Claude config directory based on platform."""
    if sys.platform == "win32":
        path = Path(Path.home(), "AppData", "Roaming", "Claude")
    elif sys.platform == "darwin":
        path = Path(Path.home(), "Library", "Application Support", "Claude")
    else:
        return None

    if path.exists():
        return path
    return None


def update_claude_config(
    file: Path,
    server_name: Optional[str] = None,
    *,
    with_editable: Optional[Path] = None,
    with_packages: Optional[list[str]] = None,
    force: bool = False,
) -> bool:
    """Add the MCP server to Claude's configuration.

    Args:
        file: Path to the server file
        server_name: Optional custom name for the server. If not provided,
                    defaults to the file stem
        with_editable: Optional directory to install in editable mode
        with_packages: Optional list of additional packages to install
        force: If True, replace existing server with same name
    """
    config_dir = get_claude_config_path()
    if not config_dir:
        return False

    config_file = config_dir / "claude_desktop_config.json"
    if not config_file.exists():
        return False

    try:
        config = json.loads(config_file.read_text())
        if "mcpServers" not in config:
            config["mcpServers"] = {}

        # Use provided server_name or fall back to file stem
        name = server_name or file.stem
        if name in config["mcpServers"]:
            if not force:
                logger.warning(
                    f"Server '{name}' already exists in Claude config. "
                    "Use `--force` to replace.",
                    extra={"config_file": str(config_file)},
                )
                return False
            logger.info(
                f"Replacing existing server '{name}' in Claude config",
                extra={"config_file": str(config_file)},
            )

        # Build uv run command
        args = ["run"]

        if with_editable:
            args.extend(["--with-editable", str(with_editable)])

        # Always include fastmcp
        args.extend(["--with", "fastmcp"])

        # Add additional packages
        if with_packages:
            for pkg in with_packages:
                if pkg:
                    args.extend(["--with", pkg])

        args.append(str(file))

        config["mcpServers"][name] = {
            "command": "uv",
            "args": args,
        }

        config_file.write_text(json.dumps(config, indent=2))
        logger.info(
            f"Added server '{name}' to Claude config",
            extra={"config_file": str(config_file)},
        )
        return True
    except Exception as e:
        logger.error(
            "Failed to update Claude config",
            extra={
                "error": str(e),
                "config_file": str(config_file),
            },
        )
        return False
