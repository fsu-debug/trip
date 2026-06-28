"""Logging setup and MCP middleware for the TRIP MCP server."""
from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

LOG = logging.getLogger("trip.mcp")


def setup_logging() -> logging.Logger:
    level_name = os.environ.get("TRIP_MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = os.environ.get(
        "TRIP_MCP_LOG_FORMAT",
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout, force=True)

    if level <= logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)
    else:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    return LOG


def truncate(value: Any, max_len: int = 500) -> str:
    text = repr(value)
    if len(text) > max_len:
        return f"{text[:max_len]}..."
    return text


def log_startup_config() -> None:
    api_url = os.environ.get("TRIP_API_URL", "http://localhost:8080")
    api_token = os.environ.get("TRIP_API_TOKEN", "")
    username = os.environ.get("TRIP_USERNAME", "")

    LOG.info("MCP server starting")
    LOG.info("TRIP_API_URL=%s", api_url)
    if api_token:
        LOG.info("TRIP_API_TOKEN is set (%d chars)", len(api_token))
    else:
        LOG.warning("TRIP_API_TOKEN is not set")
    if username:
        LOG.info("TRIP_USERNAME is set (password fallback available)")
    LOG.info("TRIP_MCP_LOG_LEVEL=%s", os.environ.get("TRIP_MCP_LOG_LEVEL", "INFO"))


class TripToolLoggingMiddleware(Middleware):
    """Log MCP tool calls, arguments, timing, and errors."""

    def __init__(self) -> None:
        self.logger = logging.getLogger("trip.mcp.tools")

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool_name = context.message.name
        args = context.message.arguments or {}
        self.logger.info("tool call: %s args=%s", tool_name, truncate(args, 1000))
        start = time.perf_counter()
        try:
            result = await call_next(context)
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.logger.info(
                "tool ok: %s (%.0fms) result=%s",
                tool_name,
                elapsed_ms,
                truncate(result, 500),
            )
            return result
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.logger.error(
                "tool failed: %s (%.0fms) %s: %s",
                tool_name,
                elapsed_ms,
                type(exc).__name__,
                exc,
            )
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(traceback.format_exc())
            raise