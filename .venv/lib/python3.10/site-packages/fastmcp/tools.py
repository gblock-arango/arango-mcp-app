"""Tool management for FastMCP."""

import base64
import inspect
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from mcp.types import ImageContent
from pydantic import BaseModel, Field, TypeAdapter, validate_call

from .exceptions import ToolError
from .utilities.logging import get_logger

logger = get_logger(__name__)


class Image:
    """Helper class for returning images from tools."""

    def __init__(
        self,
        path: Optional[Union[str, Path]] = None,
        data: Optional[bytes] = None,
        format: Optional[str] = None,
    ):
        if path is None and data is None:
            raise ValueError("Either path or data must be provided")
        if path is not None and data is not None:
            raise ValueError("Only one of path or data can be provided")

        self.path = Path(path) if path else None
        self.data = data
        self._format = format
        self._mime_type = self._get_mime_type()

    def _get_mime_type(self) -> str:
        """Get MIME type from format or guess from file extension."""
        if self._format:
            return f"image/{self._format.lower()}"

        if self.path:
            suffix = self.path.suffix.lower()
            return {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(suffix, "application/octet-stream")
        return "image/png"  # default for raw binary data

    def to_image_content(self) -> ImageContent:
        """Convert to MCP ImageContent."""
        if self.path:
            with open(self.path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
        else:
            data = base64.b64encode(self.data).decode()

        return ImageContent(type="image", data=data, mimeType=self._mime_type)


class Tool(BaseModel):
    """Internal tool registration info."""

    func: Callable = Field(exclude=True)
    name: str = Field(description="Name of the tool")
    description: str = Field(description="Description of what the tool does")
    parameters: dict = Field(description="JSON schema for tool parameters")
    is_async: bool = Field(description="Whether the tool is async")

    @classmethod
    def from_function(
        cls,
        func: Callable,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> "Tool":
        """Create a Tool from a function."""
        func_name = name or func.__name__

        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        func_doc = description or func.__doc__ or ""
        is_async = inspect.iscoroutinefunction(func)

        # Get schema from TypeAdapter - will fail if function isn't properly typed
        parameters = TypeAdapter(func).json_schema()

        # ensure the arguments are properly cast
        func = validate_call(func)

        return cls(
            func=func,
            name=func_name,
            description=func_doc,
            parameters=parameters,
            is_async=is_async,
        )

    async def run(self, arguments: dict) -> Any:
        """Run the tool with arguments."""
        try:
            # Call function with proper async handling
            if self.is_async:
                return await self.func(**arguments)
            return self.func(**arguments)
        except Exception as e:
            raise ToolError(f"Error executing tool {self.name}: {e}") from e


class ToolManager:
    """Manages FastMCP tools."""

    def __init__(self, warn_on_duplicate_tools: bool = True):
        self._tools: Dict[str, Tool] = {}
        self.warn_on_duplicate_tools = warn_on_duplicate_tools

    def get_tool(self, name: str) -> Optional[Tool]:
        """Get tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """List all registered tools."""
        return list(self._tools.values())

    def add_tool(
        self,
        func: Callable,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Tool:
        """Add a tool to the server."""
        tool = Tool.from_function(func, name=name, description=description)
        existing = self._tools.get(tool.name)
        if existing:
            if self.warn_on_duplicate_tools:
                logger.warning(f"Tool already exists: {tool.name}")
            return existing
        self._tools[tool.name] = tool
        return tool

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Call a tool by name with arguments."""
        tool = self.get_tool(name)
        if not tool:
            raise ToolError(f"Unknown tool: {name}")

        return await tool.run(arguments)
