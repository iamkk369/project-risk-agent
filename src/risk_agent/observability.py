"""Safe structured logging and request correlation for the agent runtime."""
from __future__ import annotations

import json
import logging
import os
import uuid
from contextvars import ContextVar
from typing import Any

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_configured = False


def configure_logging() -> None:
    """Configure JSON logs suitable for CloudWatch without logging secrets."""
    global _configured
    if _configured:
        return

    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "request_id": _request_id.get(),
                "service": os.getenv("SERVICE_NAME", "project-risk-agent"),
            }
            for key in ("event", "repository", "delivery_id", "duration_ms", "outcome"):
                value = getattr(record, key, None)
                if value is not None:
                    payload[key] = value
            return json.dumps(payload, separators=(",", ":"))

    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    _configured = True


def set_request_id(value: str | None = None) -> str:
    request_id = value or str(uuid.uuid4())
    _request_id.set(request_id)
    return request_id


def log_event(level: int, event: str, message: str, **fields: Any) -> None:
    """Emit structured metadata only; callers must never pass credential values."""
    record = logging.LogRecord(
        name="risk_agent",
        level=level,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.event = event
    for key in ("repository", "delivery_id", "duration_ms", "outcome"):
        if key in fields:
            setattr(record, key, fields[key])
    logging.getLogger("risk_agent").handle(record)
