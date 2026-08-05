"""Secret redaction for worker logs and payload guards.

Kept dependency-free and local to the worker so the worker never imports API
internals. Mirrors the API's redaction rules.
"""

import re

_REDACTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?P<prefix>://[^/\s@:]+:)[^@\s]+(?P<suffix>@)"),
    re.compile(
        r"(?P<prefix>\b(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key)\b\s*[=:]\s*)\S+",
        re.IGNORECASE,
    ),
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


def contains_credential_shape(text: str) -> bool:
    return redact(text) != text
