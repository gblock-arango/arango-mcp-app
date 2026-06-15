"""FastMCP - A more ergonomic interface for MCP servers."""

import asyncio
import functools
import json
import logging
from typing import Any, Callable, Optional, Sequence, Union, Literal

import pydantic.json
from mcp.server import Server as MCPServer
from mcp.server.stdio import stdio_server
from mcp.server.sse import SseServerTransport
from mcp.types import (
    Resource as MCPResource,
    Tool,
    TextContent,
    ImageContent,
)
from pydantic_settings import BaseSettings
from pydantic.networks import _BaseUrl

from .exceptions import ResourceError
from .resources import Resource, FunctionResource, ResourceManager
from .tools import ToolManager, Image
from .utilities.logging import get_logger, configure_logging

logger = get_logger(__name__)


class Settings(BaseSettings):
    """FastMCP server settings.

    All settings can be configured via environment variables with the prefix FASTMCP_.
    For example, FASTMCP_DEBUG=true will set debug=True.
    """

    model_config: dict = dict(env_prefix="FASTMCP_")

    # Server settings
    debug: bool = False
    log_level: Literal[
        logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL
    ] = logging.INFO

    # HTTP settings
    host: str = "0.0.0.0"
    port: int = 8000

    # resource settings
    warn_on_duplicate_resources: bool = True

    # tool settings
    warn_on_duplicate_tools: bool = True


class FastMCP:
    def __init__(self, name=None, **settings: Optional[Settings]):
        self.settings = Settings(**settings)
        self._mcp_server = MCPServer(name=name or "FastMCP")
        self._tool_manager = ToolManager(
            warn_on_duplicate_tools=self.settings.warn_on_duplicate_tools
        )
        self._resource_manager = ResourceManager(
            warn_on_duplicate_resources=self.settings.warn_on_duplicate_resources
        )

        # Set up MCP protocol handlers
        self._setup_handlers()

        # Configure logging
        configure_logging(self.settings.log_level)

    @property
    def name(self) -> str:
        return self._mcp_server.name

    def run(self, transport: Literal["stdio", "sse"] = "stdio") -> None:
        """Run the FastMCP server. Note this is a synchronous function.

        Args:
            transport: Transport protocol to use ("stdio" or "sse")
        """
        TRANSPORTS = Literal["stdio", "sse"]
        if transport not in TRANSPORTS.__args__:  # type: ignore
            raise ValueError(f"Unknown transport: {transport}")

        if transport == "stdio":
            asyncio.run(self.run_stdio_async())
        else:  # transport == "sse"
            asyncio.run(self.run_sse_async())

    def _setup_handlers(self) -> None:
        """Set up core MCP protocol handlers."""
        self._mcp_server.list_tools()(self.list_tools)
        self._mcp_server.call_tool()(self.call_tool)
        self._mcp_server.list_resources()(self.list_resources)
        self._mcp_server.read_resource()(self.read_resource)

    async def list_tools(self) -> list[Tool]:
        """List all available tools."""
        tools = self._tool_manager.list_tools()
        return [
            Tool(
                name=info.name,
                description=info.description,
                inputSchema=info.parameters,
            )
            for info in tools
        ]

    async def call_tool(
        self, name: str, arguments: dict
    ) -> Sequence[Union[TextContent, ImageContent]]:
        """Call a tool by name with arguments."""
        try:
            result = await self._tool_manager.call_tool(name, arguments)
            return self._convert_to_content(result)
        except Exception as e:
            logger.error(f"Error calling tool {name}: {e}")
            return [
                TextContent(
                    type="text",
                    text=str(e),
                    is_error=True,
                )
            ]

    async def list_resources(self) -> list[MCPResource]:
        """List all available resources."""

        resources = self._resource_manager.list_resources()
        return [
            MCPResource(
                uri=resource.uri,
                name=resource.name,
                description=resource.description,
                mimeType=resource.mime_type,
            )
            for resource in resources
        ]

    async def read_resource(self, uri: _BaseUrl) -> Union[str, bytes]:
        """Read a resource by URI."""
        resource = self._resource_manager.get_resource(uri)
        if not resource:
            raise ResourceError(f"Unknown resource: {uri}")

        try:
            return await resource.read()
        except Exception as e:
            logger.error(f"Error reading resource {uri}: {e}")
            raise ResourceError(str(e))

    def _convert_to_content(
        self, value: Any
    ) -> Sequence[Union[TextContent, ImageContent]]:
        """Convert a tool result to MCP content types."""

        # Already a sequence of valid content types
        if isinstance(value, (list, tuple)):
            if all(isinstance(x, (TextContent, ImageContent)) for x in value):
                return value
            # Handle mixed content including Image objects
            result = []
            for item in value:
                if isinstance(item, (TextContent, ImageContent)):
                    result.append(item)
                elif isinstance(item, Image):
                    result.append(item.to_image_content())
                else:
                    result.append(
                        TextContent(
                            type="text",
                            text=json.dumps(
                                item, indent=2, default=pydantic.json.pydantic_encoder
                            ),
                        )
                    )
            return result

        # Single content type
        if isinstance(value, (TextContent, ImageContent)):
            return [value]

        # Image helper
        if isinstance(value, Image):
            return [value.to_image_content()]

        # All other types - convert to JSON string with pydantic encoder
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    value, indent=2, default=pydantic.json.pydantic_encoder
                ),
            )
        ]

    def add_tool(
        self,
        func: Callable,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        """Add a tool to the server."""
        self._tool_manager.add_tool(func, name=name, description=description)

    def tool(
        self, name: Optional[str] = None, description: Optional[str] = None
    ) -> Callable:
        """Decorator to register a tool."""
        # Check if user passed function directly instead of calling decorator
        if callable(name):
            raise TypeError(
                "The @tool decorator was used incorrectly. "
                "Did you forget to call it? Use @tool() instead of @tool"
            )

        def decorator(func: Callable) -> Callable:
            self.add_tool(func, name=name, description=description)
            return func

        return decorator

    def add_resource(self, resource: Resource) -> None:
        """Add a resource to the server.

        Args:
            resource: A Resource instance to add
        """
        self._resource_manager.add_resource(resource)

    def resource(
        self,
        uri: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Callable:
        """Decorator to register a function as a resource.

        The function will be called when the resource is read to generate its content.

        Args:
            uri: URI for the resource (e.g. "resource://my-resource")
            description: Optional description of the resource
            mime_type: Optional MIME type for the resource

        Example:
            @server.resource("resource://my-resource")
            def get_data() -> str:
                return "Hello, world!"
        """
        # Check if user passed function directly instead of calling decorator
        if callable(uri):
            raise TypeError(
                "The @resource decorator was used incorrectly. "
                "Did you forget to call it? Use @resource('uri') instead of @resource"
            )

        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper() -> Any:
                return func()

            resource = FunctionResource(
                uri=uri,
                name=name,
                description=description,
                mime_type=mime_type or "text/plain",
                func=wrapper,
            )
            self.add_resource(resource)
            return wrapper

        return decorator

    async def run_stdio_async(self) -> None:
        """Run the server using stdio transport."""
        async with stdio_server() as (read_stream, write_stream):
            await self._mcp_server.run(
                read_stream,
                write_stream,
                self._mcp_server.create_initialization_options(),
            )

    async def run_sse_async(self) -> None:
        """Run the server using SSE transport."""
        from starlette.applications import Starlette
        from starlette.routing import Route
        import uvicorn

        sse = SseServerTransport("/messages")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await self._mcp_server.run(
                    streams[0],
                    streams[1],
                    self._mcp_server.create_initialization_options(),
                )

        async def handle_messages(request):
            await sse.handle_post_message(request.scope, request.receive, request._send)

        starlette_app = Starlette(
            debug=self.settings.debug,
            routes=[
                Route("/sse", endpoint=handle_sse),
                Route("/messages", endpoint=handle_messages, methods=["POST"]),
            ],
        )

        uvicorn.run(
            starlette_app,
            host=self.settings.host,
            port=self.settings.port,
            log_level=self.settings.log_level,
        )
