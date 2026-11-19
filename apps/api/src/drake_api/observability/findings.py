"""What needs attention in an environment, found from the overview (ADR-0033).

Pure: no I/O, no clock. Each detector reads sections the environment
overview already answered (observability.overview) and returns findings:
a kind, a severity, the bindings involved, and the numbers that made it a
finding. Wording belongs to the screen; the API carries facts and keys.

A detector only speaks when the sections it reads answered `ok`. A store
that could not be read says nothing about health, so it never produces a
finding and never hides one; the screen states the section as unavailable.

Thresholds are deliberately conservative. A finding the team learns to
ignore costs more than one that shows up an hour later.
"""

import statistics
from typing import Any

# periodic_error_logs: error or worse lines that keep coming at a steady
# rate, the signature of something scheduled (a probe, a cron) failing the
# same way every time rather than an incident.
ERROR_SEVERITY = 17
PERIODIC_MIN_LINES = 100
PERIODIC_MIN_COVERAGE = 0.9  # share of buckets with error lines
PERIODIC_MAX_VARIATION = 0.3  # coefficient of variation of errors per bucket
PERIODIC_MIN_BUCKETS = 6  # full buckets of history since the first error line

# connection_per_query: a service opening a database connection for a large
# share of its queries, the signature of a missing connection pool.
CONNECT_OPERATIONS = ("connect", "connection", "open")
CONNECT_MIN_CALLS = 100
CONNECT_MIN_RATIO = 0.2  # new connections per query

# slow_call: one bound service waiting on another for over a second.
SLOW_CALL_P95_MS = 1000.0
SLOW_CALL_MIN_CALLS = 10

# client_errors: a large share of a service's answers are 4xx.
CLIENT_ERROR_MIN = 20
CLIENT_ERROR_MIN_SHARE = 0.05
AUTH_CODES = ("401", "403")


def _ok(section: dict[str, Any] | None) -> Any:
    if not section or section.get("state") != "ok":
        return None
    return section.get("data")


def _is_connect(operation: str) -> bool:
    head = operation.strip().lower().split(" ", 1)[0]
    return head in CONNECT_OPERATIONS


def periodic_error_logs(
    log_lines: list[dict[str, Any]], log_patterns: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    errors = [b["errors"] for b in log_lines]
    if not errors or sum(errors) < PERIODIC_MIN_LINES:
        return []
    # Steadiness is judged from the first bucket with error lines: a line
    # that started (or started being read as an error) mid-window is still
    # periodic since then. The newest bucket is still filling and is left
    # out. Too short a history says nothing about a period.
    first = next(i for i, e in enumerate(errors) if e > 0)
    full = errors[first:-1]
    if len(full) < PERIODIC_MIN_BUCKETS:
        return []
    covered = sum(1 for e in full if e > 0) / len(full)
    mean = statistics.fmean(full)
    variation = statistics.pstdev(full) / mean if mean else 1.0
    if covered < PERIODIC_MIN_COVERAGE or variation > PERIODIC_MAX_VARIATION:
        return []
    worst = [
        p
        for p in log_patterns
        if p["severity_number"] >= ERROR_SEVERITY and p["lines"] >= PERIODIC_MIN_LINES
    ]
    if not worst:
        return []
    top = max(worst, key=lambda p: p["lines"])
    return [
        {
            "kind": "periodic_error_logs",
            "severity": "warning",
            "binding_ids": top.get("binding_ids", []),
            "metrics": {
                "lines": top["lines"],
                "severity_text": top["severity_text"],
                "pattern": top["pattern"],
                "per_bucket": round(mean, 1),
                "since": log_lines[first]["t"],
                "coverage": round(covered, 3),
                "variation": round(variation, 3),
                "error_lines_total": sum(errors),
            },
        }
    ]


def connection_per_query(databases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[tuple[str, str | None, str | None], dict[str, Any]] = {}
    for row in databases:
        key = (row["binding_id"], row["system"], row["database"])
        entry = by.setdefault(key, {"connects": 0, "queries": 0, "connect_p95_ms": None})
        if _is_connect(row["operation"]):
            entry["connects"] += row["calls"]
            entry["connect_p95_ms"] = row["p95_ms"]
        else:
            entry["queries"] += row["calls"]
    out: list[dict[str, Any]] = []
    for (binding_id, system, database), entry in by.items():
        connects, queries = entry["connects"], entry["queries"]
        if connects < CONNECT_MIN_CALLS or not queries:
            continue
        ratio = connects / queries
        if ratio < CONNECT_MIN_RATIO:
            continue
        out.append(
            {
                "kind": "connection_per_query",
                "severity": "warning",
                "binding_ids": [binding_id],
                "metrics": {
                    "system": system,
                    "database": database,
                    "connects": connects,
                    "queries": queries,
                    "ratio": round(ratio, 3),
                    "connect_p95_ms": entry["connect_p95_ms"],
                },
            }
        )
    return sorted(out, key=lambda f: -f["metrics"]["connects"])


def slow_calls(calls: list[dict[str, Any]], pooled: set[str] | None = None) -> list[dict[str, Any]]:
    """`pooled` is the set of bindings a connection_per_query finding named:
    a slow call into one of them most likely waits on its connecting."""
    out: list[dict[str, Any]] = []
    for call in calls:
        p95 = call["p95_ms"]
        if p95 is None or p95 < SLOW_CALL_P95_MS or call["calls"] < SLOW_CALL_MIN_CALLS:
            continue
        out.append(
            {
                "kind": "slow_call",
                "severity": "warning",
                "binding_ids": [call["from_binding_id"], call["to_binding_id"]],
                "metrics": {
                    "calls": call["calls"],
                    "errors": call["errors"],
                    "p95_ms": p95,
                    "likely_cause": (
                        "connection_per_query"
                        if pooled and call["to_binding_id"] in pooled
                        else None
                    ),
                },
            }
        )
    return sorted(out, key=lambda f: -f["metrics"]["p95_ms"])


def client_errors(codes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in codes:
        total = sum(row["codes"].values())
        fours = {c: n for c, n in row["codes"].items() if c.startswith("4")}
        count = sum(fours.values())
        if count < CLIENT_ERROR_MIN or not total or count / total < CLIENT_ERROR_MIN_SHARE:
            continue
        auth_only = all(c in AUTH_CODES for c in fours)
        out.append(
            {
                "kind": "client_errors",
                "severity": "info" if auth_only else "warning",
                "binding_ids": [row["binding_id"]],
                "metrics": {
                    "client_errors": count,
                    "requests": total,
                    "share": round(count / total, 4),
                    "codes": dict(sorted(fours.items(), key=lambda kv: -kv[1])),
                    "auth_only": auth_only,
                },
            }
        )
    return sorted(out, key=lambda f: -f["metrics"]["client_errors"])


def find(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Every finding the answered sections support, warnings first.

    The result says which detectors could run: a detector whose sections did
    not answer is listed in `skipped`, so "no findings" is never claimed for
    a part of the environment that could not be read."""
    found: list[dict[str, Any]] = []
    skipped: list[str] = []

    lines, patterns = _ok(sections.get("log_lines")), _ok(sections.get("log_patterns"))
    if lines is None or patterns is None:
        skipped.append("periodic_error_logs")
    else:
        found += periodic_error_logs(lines, patterns)

    databases = _ok(sections.get("databases"))
    pooled: set[str] = set()
    if databases is None:
        skipped.append("connection_per_query")
    else:
        pool = connection_per_query(databases)
        pooled = {b for f in pool for b in f["binding_ids"]}
        found += pool

    calls = _ok(sections.get("calls"))
    if calls is None:
        skipped.append("slow_call")
    else:
        found += slow_calls(calls, pooled)

    codes = _ok(sections.get("codes"))
    if codes is None:
        skipped.append("client_errors")
    else:
        found += client_errors(codes)

    order = {"warning": 0, "info": 1}
    found.sort(key=lambda f: order[f["severity"]])
    return {"items": found, "skipped": skipped}
