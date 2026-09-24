"""Thin HTTP projection for the read-only persistence-health authority."""

from __future__ import annotations

from typing import Any

from minicode.storage_health import (
    PersistenceHealthReader,
    validate_persistence_health_snapshot,
)
from minicode.web.read_model import DashboardReadError


def serve_data_health(handler: Any) -> None:
    """Serve one strict query-free schema-v1 health snapshot."""
    try:
        handler._query_params(set())
    except DashboardReadError as error:
        handler._send_read_error(error)
        return
    try:
        reader = getattr(handler.server, "persistence_health_reader", None)
        if reader is None:
            read_model = handler._dashboard_read_model()
            reader = PersistenceHealthReader(
                read_model.workspace,
                data_dir=read_model.data_dir,
            )
            setattr(handler.server, "persistence_health_reader", reader)
        payload = validate_persistence_health_snapshot(reader.snapshot())
        handler._send_json(payload)
    except Exception:  # noqa: BLE001 - source and exception text are sensitive
        handler._send_read_failure(
            "data_health_failed",
            "Data health could not be generated.",
        )


__all__ = ["serve_data_health"]
