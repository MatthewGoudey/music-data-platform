"""Logging: one line of JSON per event, so GitHub Actions and Fly logs stay greppable."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(json_lines: bool = True, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if json_lines:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    # httpx logs every request URL at INFO; some carry API keys as query parameters, and
    # Actions logs are public. Failures still surface through the callers' own logs.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.LoggerAdapter:
    """A logger whose .info(msg, extra={...}) keys land as JSON fields."""
    return _Adapter(logging.getLogger(name), {})


class _Adapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        extra = kwargs.pop("extra", None)
        kwargs["extra"] = {"extra": extra or {}}
        return msg, kwargs
