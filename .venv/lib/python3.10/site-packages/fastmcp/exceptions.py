"""Custom exceptions for FastMCP."""


class FastMCPError(Exception):
    """Base error for FastMCP."""


class ValidationError(FastMCPError):
    """Error in validating parameters or return values."""


class ResourceError(FastMCPError):
    """Error in resource operations."""


class ToolError(FastMCPError):
    """Error in tool operations."""
