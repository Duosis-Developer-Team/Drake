"""Structured JSON logging with secret redaction.

Log records are emitted as single-line JSON with the active correlation ID.
A redaction filter masks credential-shaped content so that connection strings,
passwords, and tokens can never reach log output even by accident.
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from drake_api.correlation import correlation_id_var

_REDACTION_PATTERNS: list[re.Pattern[str]] = [
    # user:password@host in URLs
    re.compile(r"(?P<prefix>://[^/\s@:]+:)[^@\s]+(?P<suffix>@)"),
    # key=value / key: value credential assignments
    re.compile(
        r"(?P<prefix>\b(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key)\b\s*[=:]\s*)\S+",
        re.IGNORECASE,
    ),
    # bearer tokens
    re.compile(r"(?P<prefix>\bbearer\s+)[a-z0-9._~+/=-]{8,}", re.IGNORECASE),
]

REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    """Mask credential-shaped substrings in *text*."""
    for pattern in _REDACTION_PATTERNS:
        if pattern.groupindex.get("suffix"):
            text = pattern.sub(rf"\g<prefix>{REDACTED}\g<suffix>", text)
        else:
            text = pattern.sub(rf"\g<prefix>{REDACTED}", text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = correlation_id_var.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
