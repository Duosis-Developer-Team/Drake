"""Log patterns: the lines a service writes, grouped by their shape.

A busy service writes the same few messages thousands of times with a
different order id, duration or address in each. Read line by line that is
an endless scroll; grouped by shape it is a short list — "payment charge
failed: upstream timeout after <n>ms …, 118 times" — that says what the service
is actually saying, and how often.

The grouping runs in the store, over every matching line in the window,
not over the page the browser happened to load. The variable parts are
replaced in a fixed order (timestamps, UUIDs, addresses, long hex ids,
numbers); the patterns are typed query parameters, never SQL text.

The same window, severity floor and plain-text term as the log read apply,
validated by the log read's own builder, so the two can never disagree
about which lines are in scope. Pattern text and samples are redacted again
on the way out, as log bodies are.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from drake_api.logging import redact
from drake_api.observability.logs import build_log_query
from drake_api.observability.scope import SCOPE_PREDICATE, parse_store_time
from drake_api.observability.store import safe_identifier

MAX_PATTERNS = 50
# How much of a line takes part in its shape. Long payloads past this point
# rarely change what a message means and would split one pattern in many.
PATTERN_CHARS = 240
MAX_SAMPLE_CHARS = 1024

# Variable parts, replaced in this order. RE2 syntax (ClickHouse).
_REPLACEMENTS: tuple[tuple[str, str, str], ...] = (
    ("re_time", r"\d{4}-\d{2}-\d{2}[T ][0-9:.,]+(Z|[+-]\d{2}:?\d{2})?", "<time>"),
    (
        "re_uuid",
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "<uuid>",
    ),
    ("re_ip", r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b", "<ip>"),
    ("re_hex", r"\b[0-9a-fA-F]{12,}\b", "<hex>"),
    ("re_num", r"\d+", "<n>"),
)


def _shape(expression: str) -> str:
    for name, _pattern, placeholder in _REPLACEMENTS:
        expression = f"replaceRegexpAll({expression}, {{{name}:String}}, '{placeholder}')"
    return expression


_STATEMENT = (
    """
SELECT
    pattern,
    count() AS lines,
    max(SeverityNumber) AS severity_number,
    argMax(SeverityText, SeverityNumber) AS severity_text,
    toString(min(Timestamp)) AS first_ts,
    toString(max(Timestamp)) AS last_ts,
    substring(argMax(Body, Timestamp), 1, {max_sample:UInt32}) AS latest,
    substring(argMin(Body, Timestamp), 1, {max_sample:UInt32}) AS earliest
FROM (
    SELECT
        Timestamp,
        Body,
        SeverityNumber,
        SeverityText,
        left("""
    + _shape("Body")
    + """, {pattern_chars:UInt32}) AS pattern
    FROM __DATABASE__.otel_logs
    WHERE Timestamp >= {start:DateTime64(9, 'UTC')}
      AND Timestamp < {end:DateTime64(9, 'UTC')}
      AND __SCOPE__
      AND SeverityNumber >= {min_severity:UInt8}
      AND ({term:String} = '' OR positionCaseInsensitive(Body, {term:String}) > 0)
)
GROUP BY pattern
ORDER BY severity_number DESC, lines DESC
LIMIT {max_patterns:UInt8}
"""
)


@dataclass(frozen=True)
class PatternQuery:
    statement: str
    parameters: dict[str, str]
    max_rows: int
    start: datetime
    end: datetime


def build_pattern_query(
    *,
    database: str,
    binding: dict[str, Any],
    range_key: str,
    severity: str,
    term: str,
    now: datetime,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> PatternQuery:
    """Raises LogQueryError for anything the log read would refuse."""
    base = build_log_query(
        database=database,
        binding=binding,
        range_key=range_key,
        severity=severity,
        term=term,
        limit=1,
        before=None,
        now=now,
        window_start=window_start,
        window_end=window_end,
    )
    parameters = {
        key: value for key, value in base.parameters.items() if key not in ("limit", "max_body")
    }
    # ClickHouse reads String parameters in its escaped format, where ``
    # is a backspace and would silently break the word boundaries. Every
    # backslash is doubled so the regex reaches RE2 as written.
    parameters.update({name: pattern.replace("\\", "\\\\") for name, pattern, _ in _REPLACEMENTS})
    parameters.update(
        {
            "pattern_chars": str(PATTERN_CHARS),
            "max_sample": str(MAX_SAMPLE_CHARS),
            "max_patterns": str(MAX_PATTERNS),
        }
    )
    statement = _STATEMENT.replace("__DATABASE__", safe_identifier(database)).replace(
        "__SCOPE__", SCOPE_PREDICATE.strip()
    )
    return PatternQuery(
        statement=statement,
        parameters=parameters,
        max_rows=MAX_PATTERNS,
        start=base.start,
        end=base.end,
    )


def _count(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("count")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError("count")


def normalize_patterns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        pattern = row.get("pattern")
        latest = row.get("latest")
        earliest = row.get("earliest")
        if not all(isinstance(value, str) for value in (pattern, latest, earliest)):
            raise ValueError("pattern")
        severity_number = row.get("severity_number")
        if not isinstance(severity_number, int):
            raise ValueError("severity_number")
        out.append(
            {
                "pattern": redact(str(pattern)),
                "lines": _count(row.get("lines")),
                "severity": str(row.get("severity_text") or "") or None,
                "severity_number": severity_number,
                "first_at": parse_store_time(row.get("first_ts")).isoformat(),
                "last_at": parse_store_time(row.get("last_ts")).isoformat(),
                "latest": redact(str(latest)),
                "earliest": redact(str(earliest)),
            }
        )
    return out
