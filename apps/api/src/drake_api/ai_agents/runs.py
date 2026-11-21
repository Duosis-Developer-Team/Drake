"""One agent's runs, read from the trace store when someone looks.

Rollups answer "how much"; a run answers "what happened": which models the
agent asked, which tools it called, in what order, how long each took and
what it cost in tokens. Runs are not copied into the database — the trace
store already keeps them for its retention, so a run view is one bounded
query.

Two statements:

- the run list: this agent's GenAI spans in a window, grouped by trace.
  The agent filter is built from the same per-span identity expressions
  discovery groups by, so a run belongs to exactly the agent its rollups
  were counted for;
- one run: the spans of one trace, each attributed with `identity_of` —
  the function discovery itself uses — so the answer to "whose step is
  this" is the same everywhere.

What a caller may see of a run: the agent's own service, and the GenAI
steps of agents the caller may see. Everything else in the trace (other
teams' services) is left out and the tree closes over the gap. A trace in
which this agent did nothing is a 404, the same answer as a trace that
does not exist, so a trace id cannot be used to probe.

No content: prompts, answers and tool arguments are dropped at the
gateway; this module reads names, counts and durations only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from drake_api.ai_agents.discovery import (
    AGENT_ID,
    AGENT_NAME,
    AGENT_OF_TRACE,
    CLUSTER,
    GENAI,
    GENERIC_NAMES,
    INFERENCE,
    INFERENCE_OPS,
    INGEST,
    NAMESPACE,
    OP,
    PROVIDER,
    SVC,
    WORKLOAD,
    identity_of,
    tokens,
)
from drake_api.observability.scope import clickhouse_time
from drake_api.observability.store import safe_identifier

MAX_RUNS = 50
MAX_RUN_SPANS = 1000
RUN_LOOKBACK = timedelta(days=8)

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_SAFE_WORD = re.compile(r"^[a-z0-9_ -]+$")
_UUID_RE = "(?i)^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$"


class RunQueryError(ValueError):
    """A request the run views will not turn into a statement."""


@dataclass(frozen=True, slots=True)
class RunStatement:
    sql: str
    parameters: dict[str, str]
    max_rows: int


def _unnamed(expr: str) -> str:
    """SQL twin of `discovery.real_name` being false."""
    words = sorted(GENERIC_NAMES)
    if not all(_SAFE_WORD.match(w) for w in words):
        raise RunQueryError("generic names must be plain words")
    generic = ", ".join(f"'{w}'" for w in words)
    return (
        f"(trim(BOTH ' ' FROM {expr}) = '' OR length({expr}) > 128 "
        f"OR lower(trim(BOTH ' ' FROM {expr})) IN ({generic}) "
        f"OR match({expr}, '{_UUID_RE}') OR match({expr}, '(?i)^[0-9a-f]{{16,}}$'))"
    )


def agent_filter(agent: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """The spans that belong to this agent, as a predicate and its params."""
    confidence = agent["confidence"]
    if confidence == "stable":
        return f"({AGENT_ID}) = {{agent_id:String}}", {"agent_id": agent["agent_external_id"]}
    if confidence == "named":
        return (
            f"({AGENT_ID}) = '' AND {SVC} = {{svc:String}} "
            f"AND trim(BOTH ' ' FROM {AGENT_NAME}) = {{agent_name:String}}",
            {"svc": agent["service_name"] or "", "agent_name": agent["agent_name"] or ""},
        )
    return (
        f"({AGENT_ID}) = '' AND {_unnamed(AGENT_NAME)} AND {SVC} = {{svc:String}} "
        f"AND {CLUSTER} = {{cluster:String}} AND {NAMESPACE} = {{ns:String}} "
        f"AND {WORKLOAD} = {{wk:String}}",
        {
            "svc": agent["service_name"] or "",
            "cluster": agent["cluster_ref"] or "",
            "ns": agent["namespace"] or "",
            "wk": agent["workload"] or "",
        },
    )


_MODEL = (
    "if(t.SpanAttributes['gen_ai.response.model'] != '', "
    "t.SpanAttributes['gen_ai.response.model'], t.SpanAttributes['gen_ai.request.model'])"
)
_TOOL = "t.SpanAttributes['gen_ai.tool.name']"

_RUNS = f"""
SELECT
    t.TraceId AS trace_id,
    toString(min(t.Timestamp)) AS started,
    toUInt64(max(toUnixTimestamp64Nano(t.Timestamp) + t.Duration)
             - min(toUnixTimestamp64Nano(t.Timestamp))) AS span_ns,
    maxIf(t.Duration, {OP} = 'invoke_agent') AS agent_ns,
    countIf({OP} = 'invoke_agent') AS invocations,
    countIf({INFERENCE}) AS model_calls,
    countIf({OP} = 'execute_tool') AS tool_calls,
    countIf(t.StatusCode = 'Error') AS errors,
    sumIf({tokens("gen_ai.usage.input_tokens")}, {INFERENCE}) AS in_inf,
    sumIf({tokens("gen_ai.usage.output_tokens")}, {INFERENCE}) AS out_inf,
    sumIf({tokens("gen_ai.usage.input_tokens")}, {OP} = 'invoke_agent') AS in_agent,
    sumIf({tokens("gen_ai.usage.output_tokens")}, {OP} = 'invoke_agent') AS out_agent,
    groupUniqArrayIf(4)({_MODEL}, {INFERENCE} AND {_MODEL} != '') AS models,
    groupUniqArrayIf(6)({_TOOL}, {OP} = 'execute_tool' AND {_TOOL} != '') AS tools
FROM __DATABASE__.otel_traces AS t
LEFT JOIN ({AGENT_OF_TRACE}) AS a ON a.TraceId = t.TraceId
WHERE t.Timestamp >= {{start:DateTime64(9, 'UTC')}} AND t.Timestamp < {{end:DateTime64(9, 'UTC')}}
  AND {GENAI}
  AND __AGENT__
GROUP BY t.TraceId
__HAVING__
ORDER BY min(t.Timestamp) DESC
LIMIT {{limit:UInt16}}
"""  # noqa: S608 - fixed fragments; values are bound parameters

_RUN = f"""
SELECT
    toUnixTimestamp64Nano(t.Timestamp) AS start_ns,
    t.SpanId AS span_id,
    t.ParentSpanId AS parent_span_id,
    t.SpanName AS name,
    t.SpanKind AS kind,
    t.Duration AS duration_ns,
    t.StatusCode AS status,
    {SVC} AS svc,
    {CLUSTER} AS cluster,
    {NAMESPACE} AS ns,
    {WORKLOAD} AS wk,
    {AGENT_ID} AS agent_id,
    {AGENT_NAME} AS agent_name,
    {INGEST} AS ingest,
    t.ScopeName = '' AS ebpf,
    {GENAI} AS genai,
    {OP} AS op,
    {INFERENCE} AS inference,
    {PROVIDER} AS provider,
    {_MODEL} AS model,
    {tokens("gen_ai.usage.input_tokens")} AS input_tokens,
    {tokens("gen_ai.usage.output_tokens")} AS output_tokens,
    {tokens("gen_ai.usage.cache_read.input_tokens")} AS cache_read_tokens,
    {tokens("gen_ai.usage.reasoning.output_tokens")} AS reasoning_tokens,
    {_TOOL} AS tool,
    t.SpanAttributes['gen_ai.tool.type'] AS tool_type,
    t.SpanAttributes['mcp.method.name'] AS mcp_method,
    t.SpanAttributes['server.address'] AS server,
    t.SpanAttributes['error.type'] AS error_type,
    t.SpanAttributes['http.response.status_code'] AS http_status,
    t.SpanAttributes['http.route'] AS http_route
FROM __DATABASE__.otel_traces AS t
LEFT JOIN ({AGENT_OF_TRACE} ) AS a ON a.TraceId = t.TraceId
WHERE t.TraceId = {{trace_id:String}}
  AND t.Timestamp >= {{start:DateTime64(9, 'UTC')}} AND t.Timestamp < {{end:DateTime64(9, 'UTC')}}
ORDER BY t.Timestamp ASC
LIMIT {{max_spans:UInt16}}
"""  # noqa: S608 - fixed fragments; values are bound parameters


def build_runs(
    *,
    database: str,
    agent: dict[str, Any],
    window: timedelta,
    now: datetime,
    failed_only: bool = False,
    limit: int = MAX_RUNS,
) -> RunStatement:
    if not 1 <= limit <= MAX_RUNS:
        raise RunQueryError(f"limit must be between 1 and {MAX_RUNS}")
    predicate, params = agent_filter(agent)
    sql = (
        _RUNS.replace("__DATABASE__", safe_identifier(database))
        .replace("__AGENT__", predicate)
        .replace("__HAVING__", "HAVING errors > 0" if failed_only else "")
    )
    return RunStatement(
        sql,
        {
            "start": clickhouse_time(now - window),
            "end": clickhouse_time(now + timedelta(minutes=5)),
            "limit": str(limit),
            **params,
        },
        limit,
    )


def build_run(*, database: str, trace_id: str, now: datetime) -> RunStatement:
    trace_id = trace_id.lower()
    if not _TRACE_ID.match(trace_id) or trace_id == "0" * 32:
        raise RunQueryError("not a trace id")
    return RunStatement(
        _RUN.replace("__DATABASE__", safe_identifier(database)),
        {
            "trace_id": trace_id,
            "start": clickhouse_time(now - RUN_LOOKBACK),
            "end": clickhouse_time(now + timedelta(minutes=5)),
            "max_spans": str(MAX_RUN_SPANS),
        },
        MAX_RUN_SPANS,
    )


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _ms(ns: Any) -> float:
    return round(as_int(ns) / 1e6, 3)


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def normalize_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        model_calls = as_int(row.get("model_calls"))
        agent_ns = as_int(row.get("agent_ns"))
        has_inf = model_calls > 0 and (as_int(row.get("in_inf")) or as_int(row.get("out_inf")))
        out.append(
            {
                "trace_id": str(row.get("trace_id") or ""),
                "started_at": str(row.get("started") or "").replace(" ", "T") + "Z",
                "duration_ms": _ms(agent_ns or row.get("span_ns")),
                "invocations": as_int(row.get("invocations")),
                "model_calls": model_calls,
                "tool_calls": as_int(row.get("tool_calls")),
                "errors": as_int(row.get("errors")),
                "input_tokens": as_int(row.get("in_inf" if has_inf else "in_agent")),
                "output_tokens": as_int(row.get("out_inf" if has_inf else "out_agent")),
                "models": [str(m) for m in (row.get("models") or []) if m][:4],
                "tools": [str(x) for x in (row.get("tools") or []) if x][:6],
            }
        )
    return out


def step_kind(row: dict[str, Any]) -> str:
    op = str(row.get("op") or "")
    if op in ("invoke_agent", "create_agent"):
        return "agent"
    if op == "execute_tool" or str(row.get("mcp_method") or "") == "tools/call":
        return "tool"
    if as_int(row.get("inference")) or op in INFERENCE_OPS:
        return "model"
    if row.get("mcp_method"):
        return "mcp"
    if as_int(row.get("genai")):
        return "genai"
    return "other"


@dataclass(frozen=True, slots=True)
class Run:
    trace_id: str
    started_at: str
    duration_ms: float
    spans: list[dict[str, Any]]
    hidden: int


def assemble_run(
    rows: list[dict[str, Any]],
    *,
    trace_id: str,
    agent: dict[str, Any],
    visible_agents: dict[str, dict[str, Any]],
) -> Run | None:
    """The run as this caller may see it, or None when the agent took no
    part in the trace (or the trace is gone).

    `visible_agents` maps identity keys to `{id, display_name}` for the
    agents the caller may see; `agent` is the one being looked at.
    """
    if not rows:
        return None
    keyed: list[tuple[dict[str, Any], str | None]] = []
    mine = False
    for row in rows:
        key = identity_of(row).key if as_int(row.get("genai")) else None
        mine = mine or key == agent["identity_key"]
        keyed.append((row, key))
    if not mine:
        return None

    own_service = agent.get("service_name") or None
    shown: dict[str, tuple[dict[str, Any], str | None]] = {}
    parents: dict[str, str] = {}
    for row, key in keyed:
        span_id = str(row.get("span_id") or "")
        parents[span_id] = str(row.get("parent_span_id") or "")
        same_service = own_service is not None and row.get("svc") == own_service
        if key == agent["identity_key"] or same_service or (key and key in visible_agents):
            shown[span_id] = (row, key)

    def visible_parent(span_id: str) -> str | None:
        seen = set()
        parent = parents.get(span_id) or ""
        while parent and parent not in seen:
            if parent in shown:
                return parent
            seen.add(parent)
            parent = parents.get(parent) or ""
        return None

    t0 = min(as_int(r.get("start_ns")) for r, _ in shown.values())
    t1 = max(as_int(r.get("start_ns")) + as_int(r.get("duration_ns")) for r, _ in shown.values())
    spans = []
    for span_id, (row, key) in shown.items():
        owner = None
        if key == agent["identity_key"]:
            owner = {"id": agent["id"], "name": agent["display_name"], "self": True}
        elif key and key in visible_agents:
            other = visible_agents[key]
            owner = {"id": other["id"], "name": other["display_name"], "self": False}
        spans.append(
            {
                "span_id": span_id,
                "parent_id": visible_parent(span_id),
                "name": str(row.get("name") or ""),
                "step": step_kind(row),
                "service": _text(row.get("svc")),
                "agent": owner,
                "offset_ms": _ms(as_int(row.get("start_ns")) - t0),
                "duration_ms": _ms(row.get("duration_ns")),
                "status": "error" if row.get("status") == "Error" else "ok",
                "provider": _text(row.get("provider")),
                "model": _text(row.get("model")),
                "input_tokens": as_int(row.get("input_tokens")),
                "output_tokens": as_int(row.get("output_tokens")),
                "cache_read_tokens": as_int(row.get("cache_read_tokens")),
                "reasoning_tokens": as_int(row.get("reasoning_tokens")),
                "tool": _text(row.get("tool")),
                "tool_type": _text(row.get("tool_type")),
                "mcp_method": _text(row.get("mcp_method")),
                "server": _text(row.get("server")),
                "error_type": _text(row.get("error_type")),
                "http_status": _text(row.get("http_status")),
                "http_route": _text(row.get("http_route")),
            }
        )
    first = min(shown.values(), key=lambda item: as_int(item[0].get("start_ns")))[0]
    started = datetime.fromtimestamp(as_int(first.get("start_ns")) / 1e9, tz=UTC)
    return Run(
        trace_id=trace_id,
        started_at=started.isoformat(),
        duration_ms=_ms(t1 - t0),
        spans=spans,
        hidden=len(rows) - len(shown),
    )
