"""Which server spans are health checks and scrapes, not traffic.

A kernel-level tracer (OBI) sees every HTTP request a process answers,
including the kubelet's readiness and liveness probes and Prometheus's
scrapes. On a quiet service those are nearly all of it. In production on
2026-11-16 hermes-test's frontend answered 539 probe `GET /` an hour
against a dozen real page loads. Counted as traffic, they set the request
rate, they flatten the p95 to a probe's latency, and they keep a dead
application looking busy for as long as its probe handler answers.

So a request read (routes, totals, the request list, the timeline, the
cross-service board and golden signals from traces) leaves out:

- **A kubelet probe:** a GET or HEAD whose path and port are exactly an
  HTTP probe the workload declares in Kubernetes, sent from a bare IP. The
  probes come from the cluster agent's inventory (`http_probes` in the
  workload's spec summary). The kubelet calls from the node, which has no
  Kubernetes name, so OBI records its address as an IP literal. A request
  from another pod or from the ingress is recorded by name, so a user who
  opens the same path through the ingress is still traffic. What this
  cannot separate is a request that reaches the pod through a NodePort,
  which arrives from the node too, on exactly the probe's path and port.
- **A Prometheus scrape:** a GET of `/metrics`.

Nothing is deleted. The spans stay in the store and in a trace's detail.
Totals report how many were set aside (`health_checks`), so the screen can
say so rather than make them vanish.

SDK-instrumented services usually exclude these paths at the source. The
filter still applies to them, harmlessly. The names fall back to the older
HTTP attributes, as in observability.traces.
"""

import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# One workload declares at most this many (8 containers x 3 probe kinds).
MAX_PROBES_PER_WORKLOAD = 24
# What a probe may be: an absolute path of plain URL characters and a
# numeric port. Anything else is ignored, never escaped into a query.
_PATH = re.compile(r"/[A-Za-z0-9/_.~%+:@!$&'()*,;=-]{0,255}")
_PORT = re.compile(r"[1-9][0-9]{0,4}")

SCRAPE_PATHS = ("/metrics",)

_METHOD = (
    "if(SpanAttributes['http.request.method'] != '', "
    "SpanAttributes['http.request.method'], SpanAttributes['http.method'])"
)
_PATH_EXPR = (
    "if(SpanAttributes['url.path'] != '', SpanAttributes['url.path'], "
    "splitByChar('?', SpanAttributes['http.target'])[1])"
)
_PORT_EXPR = (
    "if(SpanAttributes['server.port'] != '', SpanAttributes['server.port'], "
    "SpanAttributes['net.host.port'])"
)
_WORKLOAD = (
    "if(ResourceAttributes['k8s.deployment.name'] != '', "
    "ResourceAttributes['k8s.deployment.name'], "
    "if(ResourceAttributes['k8s.statefulset.name'] != '', "
    "ResourceAttributes['k8s.statefulset.name'], "
    "ResourceAttributes['k8s.daemonset.name']))"
)
_FROM_A_BARE_ADDRESS = (
    "(isIPv4String(SpanAttributes['client.address']) "
    "OR isIPv6String(SpanAttributes['client.address']))"
)

# True for a span that is a health check or a scrape. Parameterised by
# `{health_checks:Array(Tuple(String, String, String, String))}`:
# (namespace, workload, path, port) for every probe of every bound workload
# the read covers.
IS_HEALTH_CHECK = f"""(
    {_METHOD} IN ('GET', 'HEAD')
    AND (
        {_PATH_EXPR} IN ({", ".join(f"'{p}'" for p in SCRAPE_PATHS)})
        OR (
            {_FROM_A_BARE_ADDRESS}
            AND has({{health_checks:Array(Tuple(String, String, String, String))}},
                    (ResourceAttributes['k8s.namespace.name'], {_WORKLOAD},
                     {_PATH_EXPR}, {_PORT_EXPR}))
        )
    )
)"""

NOT_HEALTH_CHECK = f"NOT {IS_HEALTH_CHECK}"

# A server span longer than this is a long-lived connection (a server-sent
# event stream, a websocket, a kept-alive socket an eBPF probe saw), not a
# request waiting for its answer: hermes-mcp's GET /mcp streams last hours
# and read as a 6-hour p95 (2026-11-18). Proxies end real requests far
# sooner. Such spans still count as requests and errors; they stay out of
# latency percentiles only.
LONG_LIVED_SECONDS = 300
# Tracesis's own long poll: an agent's relay request waits up to 25 s for
# work by design (agents/router_relay.py), so its duration is idle time, not
# a slow answer. Counted as requests and errors; kept out of latency, or the
# listener reads as a 20-second p95 and opens a false incident (2026-11-24).
LONG_POLL_SPAN = "POST /internal/v1/agent/relay/next"
NOT_LONG_LIVED = f"(Duration < {LONG_LIVED_SECONDS * 1_000_000_000} AND SpanName != '{LONG_POLL_SPAN}')"


def probes_from_summary(spec_summary: Any) -> list[tuple[str, str]]:
    """The (path, port) pairs of a workload's HTTP probes, from the agent's
    bounded spec summary. Malformed entries are dropped, never repaired."""
    if not isinstance(spec_summary, dict):
        return []
    raw = spec_summary.get("http_probes")
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, str]] = []
    for entry in raw[:MAX_PROBES_PER_WORKLOAD]:
        if not isinstance(entry, dict):
            continue
        path, port = entry.get("path"), entry.get("port")
        if not (isinstance(path, str) and isinstance(port, str)):
            continue
        if not (_PATH.fullmatch(path) and _PORT.fullmatch(port) and int(port) <= 65535):
            continue
        if (path, port) not in out:
            out.append((path, port))
    return out


def health_check_parameter(bindings: list[dict[str, Any]]) -> str:
    """The `health_checks` query parameter for these bindings.

    A ClickHouse Array(Tuple(String, String, String, String)) literal. It
    travels as a typed parameter, so it is parsed as data, never SQL. Every
    value was already held to a safe character set, and quotes and
    backslashes are escaped all the same.
    """
    entries: list[str] = []
    for binding in bindings:
        namespace = str(binding.get("namespace") or "")
        workload = str(binding.get("workload_name") or "")
        for path, port in binding.get("http_probes") or []:
            values = (namespace, workload, path, port)
            quoted = (v.replace("\\", "\\\\").replace("'", "\\'") for v in values)
            entries.append("(" + ",".join(f"'{v}'" for v in quoted) + ")")
    return "[" + ",".join(entries) + "]"


# The live inventory record a binding resolved to. Only its spec summary is
# read, and only the probes are kept from it.
_PROBES_SQL = """
SELECT cluster_id, uid, payload -> 'spec_summary'
FROM inventory_resources
WHERE lifecycle = 'active'
  AND (cluster_id, uid) IN (SELECT * FROM unnest(CAST(:clusters AS uuid[]), CAST(:uids AS text[])))
"""


async def with_http_probes(
    connection: AsyncConnection, bindings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Copies of *bindings*, each carrying `http_probes`: the (path, port)
    pairs its resolved workload declares. A binding that is not resolved, or
    whose workload declares none, carries an empty list."""
    pairs: list[tuple[uuid.UUID, str]] = []
    for binding in bindings:
        uid = (binding.get("resolution") or {}).get("resource_uid")
        cluster_id = (binding.get("cluster") or {}).get("id")
        if uid and cluster_id:
            pairs.append((uuid.UUID(str(cluster_id)), str(uid)))
    found: dict[tuple[str, str], list[tuple[str, str]]] = {}
    if pairs:
        rows = (
            await connection.execute(
                text(_PROBES_SQL),
                {"clusters": [p[0] for p in pairs], "uids": [p[1] for p in pairs]},
            )
        ).all()
        found = {
            (str(row[0]), str(row[1])): probes_from_summary(row[2]) for row in rows
        }
    out = []
    for binding in bindings:
        uid = (binding.get("resolution") or {}).get("resource_uid")
        cluster_id = (binding.get("cluster") or {}).get("id")
        key = (str(cluster_id), str(uid))
        out.append({**binding, "http_probes": found.get(key, [])})
    return out


def with_health_check_filter(sql: str) -> str:
    """Fill a statement's `__IS_HEALTH_CHECK__` / `__NOT_HEALTH_CHECK__` and
    `__NOT_LONG_LIVED__`."""
    return (
        sql.replace("__NOT_HEALTH_CHECK__", NOT_HEALTH_CHECK)
        .replace("__IS_HEALTH_CHECK__", IS_HEALTH_CHECK)
        .replace("__NOT_LONG_LIVED__", NOT_LONG_LIVED)
    )
