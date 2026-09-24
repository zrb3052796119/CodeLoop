"""Packaged, read-only MiniCode Dashboard web shell."""

from .http import MiniCodeWebHandler
from .mcp_current_projection import (
    McpCurrentProjection,
    McpCurrentServerProjection,
    project_current_mcp_state,
)
from .read_model import DashboardReadError, DashboardReadModel

__all__ = [
    "DashboardReadError",
    "DashboardReadModel",
    "McpCurrentProjection",
    "McpCurrentServerProjection",
    "MiniCodeWebHandler",
    "project_current_mcp_state",
]
