"""FastMCP CLI tools."""

import importlib.metadata
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

import typer
from typing_extensions import Annotated

from ..utilities.logging import get_logger
from . import claude

logger = get_logger(__name__)

app = typer.Typer(
    name="fastmcp",
    help="FastMCP development tools",
    add_completion=False,
    no_args_is_help=True,  # Show help if no args provided
)


def _build_uv_command(
    file: Path,
    with_editable: Optional[Path] = None,
    with_packages: Optional[list[str]] = None,
) -> list[str]:
    """Build the uv run command."""
    cmd = ["uv"]

    cmd.extend(["run", "--with", "fastmcp"])

    if with_editable:
        cmd.extend(["--with-editable", str(with_editable)])

    if with_packages:
        for pkg in with_packages:
            if pkg:
                cmd.extend(["--with", pkg])

    cmd.append(str(file))
    return cmd


def _parse_file_path(file_spec: str) -> Tuple[Path, Optional[str]]:
    """Parse a file path that may include a server object specification.

    Args:
        file_spec: Path to file, optionally with :object suffix

    Returns:
        Tuple of (file_path, server_object)
    """
    if ":" in file_spec:
        file_str, server_object = file_spec.rsplit(":", 1)
    else:
        file_str, server_object = file_spec, None

    file_path = Path(file_str).expanduser().resolve()
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        sys.exit(1)
    if not file_path.is_file():
        logger.error(f"Not a file: {file_path}")
        sys.exit(1)

    return file_path, server_object


def _import_server(file: Path, server_object: Optional[str] = None):
    """Import a FastMCP server from a file.

    Args:
        file: Path to the file
        server_object: Optional object name in format "module:object" or just "object"

    Returns:
        The server object
    """
    # Import the module
    spec = importlib.util.spec_from_file_location("server_module", file)
    if not spec or not spec.loader:
        logger.error("Could not load module", extra={"file": str(file)})
        sys.exit(1)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # If no object specified, try __main__ block
    if not server_object:
        # Look for the most common server object names
        for name in ["mcp", "server", "app"]:
            if hasattr(module, name):
                return getattr(module, name)

        logger.error(
            f"No server object found in {file}. Please specify the object name with file:object syntax.",
            extra={"file": str(file)},
        )
        sys.exit(1)

    # Handle module:object syntax
    if ":" in server_object:
        module_name, object_name = server_object.split(":", 1)
        try:
            server_module = importlib.import_module(module_name)
            server = getattr(server_module, object_name, None)
        except ImportError:
            logger.error(
                f"Could not import module '{module_name}'",
                extra={"file": str(file)},
            )
            sys.exit(1)
    else:
        # Just object name
        server = getattr(module, server_object, None)

    if server is None:
        logger.error(
            f"Server object '{server_object}' not found",
            extra={"file": str(file)},
        )
        sys.exit(1)

    return server


@app.command()
def version() -> None:
    """Show the FastMCP version."""
    try:
        version = importlib.metadata.version("fastmcp")
        print(f"FastMCP version {version}")
    except importlib.metadata.PackageNotFoundError:
        print("FastMCP version unknown (package not installed)")
        sys.exit(1)


@app.command()
def dev(
    file_spec: str = typer.Argument(
        ...,
        help="Python file to run, optionally with :object suffix",
    ),
    with_editable: Annotated[
        Optional[Path],
        typer.Option(
            "--with-editable",
            "-e",
            help="Directory containing pyproject.toml to install in editable mode",
            exists=True,
            file_okay=False,
            resolve_path=True,
        ),
    ] = None,
    with_packages: Annotated[
        list[str],
        typer.Option(
            "--with",
            help="Additional packages to install",
        ),
    ] = [],
) -> None:
    """Run a FastMCP server with the MCP Inspector."""
    file, server_object = _parse_file_path(file_spec)

    logger.debug(
        "Starting dev server",
        extra={
            "file": str(file),
            "server_object": server_object,
            "with_editable": str(with_editable) if with_editable else None,
            "with_packages": with_packages,
        },
    )

    try:
        uv_cmd = _build_uv_command(file, with_editable, with_packages)
        # Run the MCP Inspector command
        process = subprocess.run(
            ["npx", "@modelcontextprotocol/inspector"] + uv_cmd,
            check=True,
        )
        sys.exit(process.returncode)
    except subprocess.CalledProcessError as e:
        logger.error(
            "Dev server failed",
            extra={
                "file": str(file),
                "error": str(e),
                "returncode": e.returncode,
            },
        )
        sys.exit(e.returncode)
    except FileNotFoundError:
        logger.error(
            "npx not found. Please install Node.js and npm.",
            extra={"file": str(file)},
        )
        sys.exit(1)


@app.command()
def run(
    file_spec: str = typer.Argument(
        ...,
        help="Python file to run, optionally with :object suffix",
    ),
    transport: Annotated[
        Optional[str],
        typer.Option(
            "--transport",
            "-t",
            help="Transport protocol to use (stdio or sse)",
        ),
    ] = None,
    with_editable: Annotated[
        Optional[Path],
        typer.Option(
            "--with-editable",
            "-e",
            help="Directory containing pyproject.toml to install in editable mode",
            exists=True,
            file_okay=False,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Run a FastMCP server."""
    file, server_object = _parse_file_path(file_spec)

    logger.debug(
        "Running server",
        extra={
            "file": str(file),
            "server_object": server_object,
            "transport": transport,
            "with_editable": str(with_editable) if with_editable else None,
        },
    )

    try:
        # Import and get server object
        server = _import_server(file, server_object)

        # Run the server
        kwargs = {}
        if transport:
            kwargs["transport"] = transport

        server.run(**kwargs)

    except Exception as e:
        logger.error(
            "Failed to run server",
            extra={
                "file": str(file),
                "error": str(e),
            },
        )
        sys.exit(1)


@app.command()
def install(
    file_spec: str = typer.Argument(
        ...,
        help="Python file to run, optionally with :object suffix",
    ),
    server_name: Annotated[
        Optional[str],
        typer.Option(
            "--name",
            "-n",
            help="Custom name for the server (defaults to server's name attribute or file name)",
        ),
    ] = None,
    with_editable: Annotated[
        Optional[Path],
        typer.Option(
            "--with-editable",
            "-e",
            help="Directory containing pyproject.toml to install in editable mode",
            exists=True,
            file_okay=False,
            resolve_path=True,
        ),
    ] = None,
    with_packages: Annotated[
        list[str],
        typer.Option(
            "--with",
            help="Additional packages to install",
        ),
    ] = [],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Replace existing server if one exists with the same name",
        ),
    ] = False,
) -> None:
    """Install a FastMCP server in the Claude desktop app."""
    file, server_object = _parse_file_path(file_spec)

    logger.debug(
        "Installing server",
        extra={
            "file": str(file),
            "server_name": server_name,
            "server_object": server_object,
            "with_editable": str(with_editable) if with_editable else None,
            "with_packages": with_packages,
            "force": force,
        },
    )

    if not claude.get_claude_config_path():
        logger.error("Claude app not found")
        sys.exit(1)

    # Try to import server to get its name, but fall back to file name if dependencies missing
    name = server_name
    if not name:
        try:
            server = _import_server(file, server_object)
            name = server.name
        except (ImportError, ModuleNotFoundError) as e:
            logger.debug(
                "Could not import server (likely missing dependencies), using file name",
                extra={"error": str(e)},
            )
            name = file.stem

    if claude.update_claude_config(
        file,
        name,
        with_editable=with_editable,
        with_packages=with_packages,
        force=force,
    ):
        print(f"Successfully installed {name} in Claude app")
    else:
        print(f"Failed to install {name} in Claude app")
        sys.exit(1)


if __name__ == "__main__":
    app()
