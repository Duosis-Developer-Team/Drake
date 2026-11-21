"""Agents appear the first time telemetry shows them (Agent Visibility, phase 2).

Every couple of minutes this reads the GenAI spans of the closed and current
hour from the trace store — in the one shape the gateway normalised them to
(`gen_ai.*`) — and keeps, per agent, hour, provider and model, how often it
ran, called models and tools, failed, and how many tokens it used. An agent
nobody registered gets an entry here on first sight.

Who an agent is comes from the best signal present, and that confidence is
stored with it:

- `stable`   — `gen_ai.agent.id` (on the span, or on the trace's agent span);
- `named`    — `gen_ai.agent.name` within its service, when it is a real name
               and not a framework default ("StateGraph") or a UUID;
- `service`  — only the service that calls a model, through an SDK;
- `observed` — only eBPF saw it (no instrumentation scope at all).

A model call rarely carries the agent's name; it is attributed to the agent
span of its own trace (the earliest `invoke_agent`), or to its service. A
span that names its own agent never borrows another agent's id from the
trace: two agents in one trace stay two.
Tokens are counted on model-call spans; an agent whose SDK reports usage
only on its agent span is counted from there instead, never both.

Read-only against the store, counts and names only, one lease per cycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.observability.scope import clickhouse_time
from drake_api.observability.store import ClickHouseStore, safe_identifier

logger = logging.getLogger("drake_api.ai_agents.discovery")

LEASE_KEY = "agents:discovery:cycle"
SETTLE = timedelta(minutes=2)
BACKFILL = timedelta(hours=71)
CHUNK = timedelta(hours=6)
MAX_ROWS = 1000

INFERENCE_OPS = ("chat", "text_completion", "generate_content", "embeddings")
# Framework defaults and generic words are not names of an agent.
_GENERIC_NAMES = frozenset(
    {
        "agent",
        "agents",
        "stategraph",
        "compiledstategraph",
        "langgraph",
        "agentexecutor",
        "runnablesequence",
        "workflow",
        "assistant",
        "default",
    }
)
GENERIC_NAMES = _GENERIC_NAMES
_UUIDISH = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.I)
_HEXISH = re.compile(r"^[0-9a-f]{16,}$", re.I)

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _r(key: str) -> str:
    return f"t.ResourceAttributes['{key}']"


def _s(key: str) -> str:
    return f"t.SpanAttributes['{key}']"


def _first(*pairs: str, last: str) -> str:
    """multiIf over attributes: the first non-empty one, else `last`."""
    arms = ", ".join(f"{value} != '', {value}" for value in pairs)
    return f"multiIf({arms}, {last})"


# Per-span identity, as expressions: discovery groups by them, the run
# views filter by them — one definition, so a run belongs to exactly the
# agent its rollups were counted for.
SVC = "t.ServiceName"
CLUSTER = _r("k8s.cluster.name")
NAMESPACE = _r("k8s.namespace.name")
WORKLOAD = _first(
    _r("k8s.deployment.name"), _r("k8s.statefulset.name"), _r("k8s.daemonset.name"), last="''"
)
AGENT_ID = f"""multiIf({_s("gen_ai.agent.id")} != '', {_s("gen_ai.agent.id")},
            {_s("gen_ai.agent.name")} != '', '',
            {_r("gen_ai.main_agent.id")} != '', {_r("gen_ai.main_agent.id")},
            a.trace_agent_id)"""
AGENT_NAME = _first(
    _s("gen_ai.agent.name"), _r("gen_ai.main_agent.name"), last="a.trace_agent_name"
)
INGEST = _r("tracesis.ingest")

_IDENTITY = f"""
    {SVC} AS svc,
    {CLUSTER} AS cluster,
    {NAMESPACE} AS ns,
    {WORKLOAD} AS wk,
    {AGENT_ID} AS agent_id,
    {AGENT_NAME} AS agent_name,
    {INGEST} AS ingest,
    max(t.ScopeName = '') AS ebpf"""

AGENT_OF_TRACE = """
    SELECT TraceId,
           argMin(SpanAttributes['gen_ai.agent.id'], Timestamp) AS trace_agent_id,
           argMin(SpanAttributes['gen_ai.agent.name'], Timestamp) AS trace_agent_name
    FROM __DATABASE__.otel_traces
    WHERE Timestamp >= {start:DateTime64(9, 'UTC')} AND Timestamp < {end:DateTime64(9, 'UTC')}
      AND SpanAttributes['gen_ai.operation.name'] = 'invoke_agent'
    GROUP BY TraceId"""

GENAI = """(t.SpanAttributes['gen_ai.operation.name'] != ''
         OR t.SpanAttributes['gen_ai.provider.name'] != ''
         OR t.SpanAttributes['gen_ai.system'] != ''
         OR t.SpanAttributes['gen_ai.request.model'] != '')"""

OP = "t.SpanAttributes['gen_ai.operation.name']"
PROVIDER = (
    "if(t.SpanAttributes['gen_ai.provider.name'] != '', t.SpanAttributes['gen_ai.provider.name'], "
    "t.SpanAttributes['gen_ai.system'])"
)
# A span with no operation name but a provider or a model is a model call
# (eBPF sees calls, not operations; some SDKs set only the model).
INFERENCE = (
    f"({OP} IN ('chat', 'text_completion', 'generate_content', 'embeddings') "
    f"OR ({OP} = '' AND ({PROVIDER} != '' OR t.SpanAttributes['gen_ai.request.model'] != '')))"
)


def tokens(key: str) -> str:
    return f"toUInt64OrZero(t.SpanAttributes['{key}'])"


_ROLLUPS = (
    "SELECT"
    + _IDENTITY
    + f""",
    {PROVIDER} AS provider,
    if(t.SpanAttributes['gen_ai.response.model'] != '', t.SpanAttributes['gen_ai.response.model'],
       t.SpanAttributes['gen_ai.request.model']) AS model,
    toUnixTimestamp(toStartOfHour(t.Timestamp)) AS hour,
    countIf({OP} = 'invoke_agent') AS invocations,
    countIf({INFERENCE}) AS inference_calls,
    countIf({OP} = 'execute_tool') AS tool_calls,
    countIf(t.StatusCode = 'Error') AS errors,
    sumIf({tokens("gen_ai.usage.input_tokens")}, {INFERENCE}) AS in_inf,
    sumIf({tokens("gen_ai.usage.output_tokens")}, {INFERENCE}) AS out_inf,
    sumIf({tokens("gen_ai.usage.input_tokens")}, {OP} = 'invoke_agent') AS in_agent,
    sumIf({tokens("gen_ai.usage.output_tokens")}, {OP} = 'invoke_agent') AS out_agent,
    sumIf({tokens("gen_ai.usage.cache_read.input_tokens")}, {INFERENCE}) AS cache_read,
    sumIf(greatest({tokens("gen_ai.usage.cache_write.input_tokens")},
                   {tokens("gen_ai.usage.cache_creation.input_tokens")}),
          {INFERENCE}) AS cache_write,
    sumIf({tokens("gen_ai.usage.reasoning.output_tokens")}, {INFERENCE}) AS reasoning,
    toUInt64(sumIf(t.Duration, {OP} = 'invoke_agent') / 1000000) AS duration_ms
FROM __DATABASE__.otel_traces AS t
LEFT JOIN ({AGENT_OF_TRACE}) AS a ON a.TraceId = t.TraceId
WHERE t.Timestamp >= {{start:DateTime64(9, 'UTC')}} AND t.Timestamp < {{end:DateTime64(9, 'UTC')}}
  AND {GENAI}
GROUP BY svc, cluster, ns, wk, agent_id, agent_name, ingest, provider, model, hour
LIMIT {{max_rows:UInt16}}"""
)

_TOOLS = (
    "SELECT"
    + _IDENTITY
    + f""",
    t.SpanAttributes['gen_ai.tool.name'] AS tool,
    t.SpanAttributes['server.address'] AS server,
    toUnixTimestamp(toStartOfHour(t.Timestamp)) AS hour,
    count() AS calls,
    countIf(t.StatusCode = 'Error') AS errors,
    toUInt64(sum(t.Duration) / 1000000) AS duration_ms
FROM __DATABASE__.otel_traces AS t
LEFT JOIN ({AGENT_OF_TRACE}) AS a ON a.TraceId = t.TraceId
WHERE t.Timestamp >= {{start:DateTime64(9, 'UTC')}} AND t.Timestamp < {{end:DateTime64(9, 'UTC')}}
  AND {OP} = 'execute_tool' AND t.SpanAttributes['gen_ai.tool.name'] != ''
GROUP BY svc, cluster, ns, wk, agent_id, agent_name, ingest, tool, server, hour
LIMIT {{max_rows:UInt16}}"""
)


# Agent → agent: inside a trace with two or more agent runs, the caller of a
# run is the agent run that encloses it in time (the latest-starting one
# that started no later and ended no earlier). Sub-agents and
# agents-as-tools run inside their caller's span, so this is the call tree
# without walking parents span by span — and it stays one aggregated,
# bounded answer: one row per caller, callee and hour.
_EDGE_SPANS = f"""
    SELECT t.TraceId AS trace_id, t.SpanId AS span_id,
           toUnixTimestamp64Nano(t.Timestamp) AS s,
           toUnixTimestamp64Nano(t.Timestamp) + t.Duration AS e,
           toUnixTimestamp(toStartOfHour(t.Timestamp)) AS hour,
           t.StatusCode = 'Error' AS failed,
           {SVC} AS svc, {CLUSTER} AS cluster, {NAMESPACE} AS ns, {WORKLOAD} AS wk,
           {AGENT_ID} AS agent_id, {AGENT_NAME} AS agent_name, {INGEST} AS ingest,
           t.ScopeName = '' AS ebpf
    FROM __DATABASE__.otel_traces AS t
    LEFT JOIN ({AGENT_OF_TRACE}) AS a ON a.TraceId = t.TraceId
    WHERE t.Timestamp >= {{start:DateTime64(9, 'UTC')}}
      AND t.Timestamp < {{end:DateTime64(9, 'UTC')}}
      AND {OP} = 'invoke_agent'
      AND t.TraceId IN (
          SELECT TraceId FROM __DATABASE__.otel_traces
          WHERE Timestamp >= {{start:DateTime64(9, 'UTC')}}
            AND Timestamp < {{end:DateTime64(9, 'UTC')}}
            AND SpanAttributes['gen_ai.operation.name'] = 'invoke_agent'
          GROUP BY TraceId HAVING count() >= 2)"""  # noqa: S608 - fixed fragments

_IDENTITY_FIELDS = ("svc", "cluster", "ns", "wk", "agent_id", "agent_name", "ingest", "ebpf")
_CALLEE = ", ".join(f"any(c.{f}) AS c_{f}" for f in _IDENTITY_FIELDS)
_CALLER = ", ".join(f"argMax(p.{f}, p.s) AS p_{f}" for f in _IDENTITY_FIELDS)
_PAIR = ", ".join([f"c_{f}" for f in _IDENTITY_FIELDS] + [f"p_{f}" for f in _IDENTITY_FIELDS])

EDGES = f"""
WITH spans AS ({_EDGE_SPANS}),
calls AS (
    SELECT any(c.hour) AS hour, any(c.failed) AS failed, {_CALLEE}, {_CALLER}
    FROM spans AS c
    INNER JOIN spans AS p ON p.trace_id = c.trace_id
    WHERE p.span_id != c.span_id AND p.s <= c.s AND p.e >= c.e AND (p.s < c.s OR p.e > c.e)
    GROUP BY c.trace_id, c.span_id
)
SELECT {_PAIR}, hour, count() AS calls, countIf(failed) AS errors
FROM calls
GROUP BY {_PAIR}, hour
LIMIT {{max_rows:UInt16}}"""  # noqa: S608 - fixed fragments; values are bound parameters


def statements(database: str) -> tuple[str, str, str]:
    db = safe_identifier(database)
    return (
        _ROLLUPS.replace("__DATABASE__", db),
        _TOOLS.replace("__DATABASE__", db),
        EDGES.replace("__DATABASE__", db),
    )


def real_name(value: str) -> bool:
    name = value.strip()
    if not name or len(name) > 128:
        return False
    if name.lower() in _GENERIC_NAMES:
        return False
    return not (_UUIDISH.match(name) or _HEXISH.match(name))


@dataclass(frozen=True)
class Identity:
    key: str
    confidence: str
    display_name: str
    agent_external_id: str | None
    agent_name: str | None
    service_name: str
    cluster_ref: str
    namespace: str
    workload: str
    ingest: str


def identity_of(row: dict[str, Any]) -> Identity:
    """The best identity this row supports, and how much to trust it."""
    svc = str(row.get("svc") or "")
    cluster = str(row.get("cluster") or "")
    ns = str(row.get("ns") or "")
    wk = str(row.get("wk") or "")
    agent_id = str(row.get("agent_id") or "").strip()
    agent_name = str(row.get("agent_name") or "").strip()
    ingest = "external" if str(row.get("ingest") or "") == "external" else "cluster"
    named = real_name(agent_name)
    common = {
        "service_name": svc[:256],
        "cluster_ref": cluster[:128],
        "namespace": ns[:128],
        "workload": wk[:256],
        "ingest": ingest,
    }
    if agent_id and len(agent_id) <= 256:
        return Identity(
            key=f"id:{agent_id}"[:512],
            confidence="stable",
            display_name=(agent_name if named else agent_id)[:200],
            agent_external_id=agent_id,
            agent_name=agent_name if named else None,
            **common,
        )
    if named:
        return Identity(
            key=f"name:{svc}:{agent_name}"[:512],
            confidence="named",
            display_name=agent_name[:200],
            agent_external_id=None,
            agent_name=agent_name,
            **common,
        )
    where = f"{cluster}/{ns}/{wk}" if (cluster or ns or wk) else ""
    if int(row.get("ebpf") or 0) and not svc:
        return Identity(
            key=f"observed:{where}"[:512],
            confidence="observed",
            display_name=(wk or ns or "unnamed workload")[:200],
            agent_external_id=None,
            agent_name=None,
            **common,
        )
    if int(row.get("ebpf") or 0):
        return Identity(
            key=f"observed:{svc}:{where}"[:512],
            confidence="observed",
            display_name=(wk or svc)[:200],
            agent_external_id=None,
            agent_name=None,
            **common,
        )
    return Identity(
        key=f"service:{svc}:{where}"[:512],
        confidence="service",
        display_name=(svc or wk or "unnamed service")[:200],
        agent_external_id=None,
        agent_name=None,
        **common,
    )


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class Bucket:
    invocations: int = 0
    inference_calls: int = 0
    tool_calls: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    duration_ms: int = 0

    def add(self, row: dict[str, Any]) -> None:
        inference = _int(row.get("inference_calls"))
        self.invocations += _int(row.get("invocations"))
        self.inference_calls += inference
        self.tool_calls += _int(row.get("tool_calls"))
        self.errors += _int(row.get("errors"))
        # Model calls carry usage; an SDK that reports it only on the agent
        # span is counted from there — never both, or it doubles.
        if inference or _int(row.get("in_inf")) or _int(row.get("out_inf")):
            self.input_tokens += _int(row.get("in_inf"))
            self.output_tokens += _int(row.get("out_inf"))
        else:
            self.input_tokens += _int(row.get("in_agent"))
            self.output_tokens += _int(row.get("out_agent"))
        self.cache_read_tokens += _int(row.get("cache_read"))
        self.cache_write_tokens += _int(row.get("cache_write"))
        self.reasoning_tokens += _int(row.get("reasoning"))
        self.duration_ms += _int(row.get("duration_ms"))


@dataclass
class Harvest:
    identities: dict[str, Identity] = field(default_factory=dict)
    seen: dict[str, tuple[datetime, datetime]] = field(default_factory=dict)
    rollups: dict[tuple[str, datetime, str, str], Bucket] = field(default_factory=dict)
    tools: dict[tuple[str, datetime, str, str], list[int]] = field(default_factory=dict)
    edges: dict[tuple[str, str, datetime], list[int]] = field(default_factory=dict)

    def _see(self, ident: Identity, hour: datetime) -> None:
        self.identities.setdefault(ident.key, ident)
        first, last = self.seen.get(ident.key, (hour, hour))
        self.seen[ident.key] = (min(first, hour), max(last, hour))

    def take_rollups(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            ident = identity_of(row)
            hour = datetime.fromtimestamp(_int(row.get("hour")), tz=UTC)
            self._see(ident, hour)
            key = (
                ident.key,
                hour,
                str(row.get("provider") or "")[:64],
                str(row.get("model") or "")[:128],
            )
            self.rollups.setdefault(key, Bucket()).add(row)

    def take_tools(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            ident = identity_of(row)
            hour = datetime.fromtimestamp(_int(row.get("hour")), tz=UTC)
            self._see(ident, hour)
            key = (
                ident.key,
                hour,
                str(row.get("tool") or "")[:128],
                str(row.get("server") or "")[:256],
            )
            agg = self.tools.setdefault(key, [0, 0, 0])
            agg[0] += _int(row.get("calls"))
            agg[1] += _int(row.get("errors"))
            agg[2] += _int(row.get("duration_ms"))

    def take_edges(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            callee = identity_of({f: row.get(f"c_{f}") for f in _IDENTITY_FIELDS})
            caller = identity_of({f: row.get(f"p_{f}") for f in _IDENTITY_FIELDS})
            if callee.key == caller.key:
                continue
            hour = datetime.fromtimestamp(_int(row.get("hour")), tz=UTC)
            self._see(callee, hour)
            self._see(caller, hour)
            agg = self.edges.setdefault((caller.key, callee.key, hour), [0, 0])
            agg[0] += _int(row.get("calls"))
            agg[1] += _int(row.get("errors"))


def floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


async def _bindings(
    connection: AsyncConnection,
) -> dict[tuple[str, str, str], tuple[Any, Any, Any]]:
    rows = (
        await connection.execute(
            text(
                """
                SELECT c.cluster_ref, b.namespace, b.workload_name, b.id, b.project_id,
                       b.environment_id
                FROM service_workload_bindings b JOIN clusters c ON c.id = b.cluster_id
                WHERE b.lifecycle = 'active'
                """
            )
        )
    ).all()
    return {(str(r[0]), str(r[1]), str(r[2])): (r[3], r[4], r[5]) for r in rows}


async def store(engine: AsyncEngine, harvest: Harvest) -> int:
    """Upsert the agents first seen or seen again, then replace the hours read."""
    if not harvest.identities:
        return 0
    async with engine.begin() as connection:
        bindings = await _bindings(connection)
        ids: dict[str, uuid.UUID] = {}
        for key, ident in harvest.identities.items():
            first, last = harvest.seen[key]
            bound = bindings.get((ident.cluster_ref, ident.namespace, ident.workload))
            row = (
                await connection.execute(
                    text(
                        """
                        INSERT INTO agents
                            (identity_key, confidence, display_name, agent_external_id,
                             agent_name, service_name, cluster_ref, namespace, workload, ingest,
                             binding_id, project_id, environment_id, first_seen_at, last_seen_at)
                        VALUES (:key, :confidence, :display, :ext, :name, :svc, :cluster, :ns,
                                :wk, :ingest, :binding, :project, :environment, :first, :last)
                        ON CONFLICT (identity_key) DO UPDATE SET
                            first_seen_at = least(agents.first_seen_at, EXCLUDED.first_seen_at),
                            last_seen_at = greatest(agents.last_seen_at, EXCLUDED.last_seen_at),
                            binding_id = coalesce(EXCLUDED.binding_id, agents.binding_id),
                            project_id = coalesce(EXCLUDED.project_id, agents.project_id),
                            environment_id = coalesce(EXCLUDED.environment_id,
                                                      agents.environment_id),
                            updated_at = now()
                        RETURNING id
                        """
                    ),
                    {
                        "key": key,
                        "confidence": ident.confidence,
                        "display": ident.display_name,
                        "ext": ident.agent_external_id,
                        "name": ident.agent_name,
                        "svc": ident.service_name,
                        "cluster": ident.cluster_ref,
                        "ns": ident.namespace,
                        "wk": ident.workload,
                        "ingest": ident.ingest,
                        "binding": bound[0] if bound else None,
                        "project": bound[1] if bound else None,
                        "environment": bound[2] if bound else None,
                        "first": first,
                        "last": last + timedelta(minutes=59, seconds=59),
                    },
                )
            ).one()
            ids[key] = uuid.UUID(str(row[0]))
        if harvest.rollups:
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_rollups
                        (agent_id, hour, provider, model, invocations, inference_calls,
                         tool_calls, errors, input_tokens, output_tokens, cache_read_tokens,
                         cache_write_tokens, reasoning_tokens, duration_ms)
                    VALUES (:agent, :hour, :provider, :model, :invocations, :inference_calls,
                            :tool_calls, :errors, :input_tokens, :output_tokens,
                            :cache_read_tokens, :cache_write_tokens, :reasoning_tokens,
                            :duration_ms)
                    ON CONFLICT (agent_id, hour, provider, model) DO UPDATE SET
                        invocations = EXCLUDED.invocations,
                        inference_calls = EXCLUDED.inference_calls,
                        tool_calls = EXCLUDED.tool_calls, errors = EXCLUDED.errors,
                        input_tokens = EXCLUDED.input_tokens,
                        output_tokens = EXCLUDED.output_tokens,
                        cache_read_tokens = EXCLUDED.cache_read_tokens,
                        cache_write_tokens = EXCLUDED.cache_write_tokens,
                        reasoning_tokens = EXCLUDED.reasoning_tokens,
                        duration_ms = EXCLUDED.duration_ms
                    """
                ),
                [
                    {
                        "agent": ids[key],
                        "hour": hour,
                        "provider": provider,
                        "model": model,
                        **bucket.__dict__,
                    }
                    for (key, hour, provider, model), bucket in harvest.rollups.items()
                ],
            )
        if harvest.tools:
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_tool_rollups (agent_id, hour, tool_name, server, calls,
                                                    errors, duration_ms)
                    VALUES (:agent, :hour, :tool, :server, :calls, :errors, :duration)
                    ON CONFLICT (agent_id, hour, tool_name, server) DO UPDATE SET
                        calls = EXCLUDED.calls, errors = EXCLUDED.errors,
                        duration_ms = EXCLUDED.duration_ms
                    """
                ),
                [
                    {
                        "agent": ids[key],
                        "hour": hour,
                        "tool": tool,
                        "server": server,
                        "calls": v[0],
                        "errors": v[1],
                        "duration": v[2],
                    }
                    for (key, hour, tool, server), v in harvest.tools.items()
                ],
            )
        if harvest.edges:
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_edges (caller_id, callee_id, hour, calls, errors)
                    VALUES (:caller, :callee, :hour, :calls, :errors)
                    ON CONFLICT (caller_id, callee_id, hour) DO UPDATE SET
                        calls = EXCLUDED.calls, errors = EXCLUDED.errors
                    """
                ),
                [
                    {
                        "caller": ids[caller],
                        "callee": ids[callee],
                        "hour": hour,
                        "calls": v[0],
                        "errors": v[1],
                    }
                    for (caller, callee, hour), v in harvest.edges.items()
                ],
            )
    return len(harvest.identities)


@dataclass
class DiscoveryReport:
    lease_acquired: bool = False
    reads: int = 0
    failed_reads: int = 0
    agents: int = 0


async def run_once(
    engine: AsyncEngine,
    store_: ClickHouseStore,
    redis: aioredis.Redis,
    *,
    database: str,
    now: datetime | None = None,
    lease_seconds: int = 300,
) -> DiscoveryReport:
    report = DiscoveryReport()
    token = uuid.uuid4().hex
    try:
        acquired = await redis.set(LEASE_KEY, token, nx=True, ex=lease_seconds)
    except Exception:
        logger.warning("agent discovery: lease unavailable, skipping")
        return report
    if not acquired:
        return report
    report.lease_acquired = True
    try:
        end = (now or datetime.now(UTC)) - SETTLE
        async with engine.connect() as connection:
            known = (
                await connection.execute(text("SELECT max(hour) FROM agent_rollups"))
            ).scalar_one_or_none()
        # Whole hours are re-read and replaced: the previous one (closing)
        # and the current one; on a first run, everything the store holds.
        start = floor_hour(end) - timedelta(hours=1) if known else floor_hour(end - BACKFILL)
        rollups_sql, tools_sql, edges_sql = statements(database)
        harvest = Harvest()
        chunk = start
        while chunk < end:
            chunk_end = min(end, chunk + CHUNK)
            params = {
                "start": clickhouse_time(chunk),
                "end": clickhouse_time(chunk_end),
                "max_rows": str(MAX_ROWS),
            }
            for sql, take in (
                (rollups_sql, harvest.take_rollups),
                (tools_sql, harvest.take_tools),
                (edges_sql, harvest.take_edges),
            ):
                report.reads += 1
                try:
                    result = await store_.query(
                        sql,
                        params,
                        max_rows=MAX_ROWS,
                        timeout_seconds=20.0,
                        correlation_id="agent-discovery",
                    )
                    rows = result.rows if isinstance(result.rows, list) else []
                except Exception:
                    report.failed_reads += 1
                    logger.warning("agent discovery: a store read failed; those hours stay as kept")
                    continue
                take(rows)
            chunk = chunk_end
        report.agents = await store(engine, harvest)
    finally:
        with contextlib.suppress(Exception):
            await redis.eval(_RELEASE, 1, LEASE_KEY, token)
    return report


class AgentDiscovery:
    """Lifespan-owned loop; exists only when agent discovery is enabled."""

    def __init__(
        self,
        engine: AsyncEngine,
        store_: ClickHouseStore,
        redis: aioredis.Redis,
        *,
        database: str,
        interval_seconds: float = 120.0,
    ) -> None:
        self._engine = engine
        self._store = store_
        self._redis = redis
        self._database = database
        self._interval = max(60.0, interval_seconds)
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="agent-discovery")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await run_once(self._engine, self._store, self._redis, database=self._database)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("agent discovery: cycle failed")
            await asyncio.sleep(self._interval)
