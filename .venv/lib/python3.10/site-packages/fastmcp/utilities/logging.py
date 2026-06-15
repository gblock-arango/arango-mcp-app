"""Logging utilities for FastMCP."""

import logging
from typing import Literal


def get_logger(name: str) -> logging.Logger:
    """Get a logger nested under FastMCP namespace.

    Args:
        name: The name of the logger, which will be prefixed with 'FastMCP.'

    Returns:
        A configured logger instance
    """
    return logging.getLogger(f"FastMCP.{name}")


def configure_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO",
) -> None:
    """Configure logging for FastMCP.

    Args:
        level: The log level to use
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
