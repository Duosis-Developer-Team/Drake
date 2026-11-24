"""The API's half of the relay: put a read on a cluster's queue, wait for it.

A cluster whose `telemetry_location` is `cluster` keeps its logs, traces and
metrics in stores inside that cluster (ADR-0037). Tracesis cannot dial them.
Its agent holds a long poll on the ingest listener instead, and this module
is how the API and the worker hand that agent a read:

    API/worker                     Redis                        listener
    ----------                     -----                        --------
    SET   relay:m:{id} = cluster   (who may answer)
    RPUSH relay:q:{cluster} item   ------------------------->   BLPOP for the
    BLPOP relay:r:{id}                                          agent's cluster
          <--------------------------------------------------   RPUSH reply

The listener (`agents.router_relay`) only pops the queue of the cluster the
calling agent's certificate belongs to, and only accepts a reply for a
request that was issued to that same cluster.

Nothing here decides WHAT is read: the store and Prometheus adapters build
the request exactly as they would for an HTTP call, and parse the reply
with the same checks. This module moves bytes and enforces deadlines.
"""

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import text

from drake_api.db import get_engine
from drake_api.settings import Settings

#: What an agent will run. Anything else is refused by the agent itself.
KINDS = ("clickhouse.query", "prometheus.query_range")

#: An agent that has not sent a heartbeat for this long is treated as gone:
#: a read fails at once instead of waiting out its whole timeout. Three
#: missed 30-second heartbeats.
HEARTBEAT_FRESH_SECONDS = 90

#: How long the list of relayed clusters is reused before it is read again.
_CACHE_SECONDS = 15.0

#: A queue nobody drains (an agent that is gone for good) expires instead of
#: growing. Every live request is far younger than this.
_QUEUE_TTL_SECONDS = 300


def queue_key(cluster_id: uuid.UUID | str) -> str:
    return f"relay:q:{cluster_id}"


def reply_key(request_id: str) -> str:
    return f"relay:r:{request_id}"


def owner_key(request_id: str) -> str:
    return f"relay:m:{request_id}"


class RelayUnavailableError(RuntimeError):
    """The read could not be relayed: agent offline, timeout, or the agent
    could not reach its own store. `code` is one of the store's bounded
    `store_*` codes, so callers report it like any other store failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RelayedCluster:
    id: uuid.UUID
    cluster_ref: str
    online: bool


@dataclass(frozen=True)
class RelayReply:
    """What the agent's store answered, as the HTTP call would have seen it."""

    status_code: int
    content_type: str
    body: bytes


#: The agent's own failure codes, mapped to the store's vocabulary. A code
#: the agent invents later is reported as `store_unreachable`, never echoed.
_AGENT_ERRORS = {
    "unreachable": "store_unreachable",
    "timeout": "store_timeout",
    "too_large": "store_response_too_large",
    "redirect": "store_redirect_refused",
    "kind_refused": "store_relay_refused",
    "request_refused": "store_relay_refused",
    "not_configured": "store_not_configured",
}


class AgentRelay:
    def __init__(
        self,
        settings: Settings,
        *,
        redis: aioredis.Redis | None = None,
        engine: Any = None,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._engine = engine
        self._cached: dict[str, RelayedCluster] | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    def _client(self) -> aioredis.Redis:
        if self._redis is None:
            # BLPOP waits for the agent's reply; redis-py 8's default read
            # timeout is shorter than a relay read may take.
            self._redis = aioredis.from_url(self._settings.redis_url, socket_timeout=60)
        return self._redis

    async def clusters(self) -> dict[str, RelayedCluster]:
        """Every cluster read through its agent, by `cluster_ref`."""
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < _CACHE_SECONDS:
            return self._cached
        async with self._lock:
            if self._cached is not None and time.monotonic() - self._cached_at < _CACHE_SECONDS:
                return self._cached
            engine = self._engine or get_engine(self._settings)
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text(
                            """
                            SELECT c.id, c.cluster_ref,
                                   coalesce(max(a.last_heartbeat_at)
                                       > now() - make_interval(secs => :fresh), false)
                            FROM clusters c
                            LEFT JOIN cluster_agents a
                              ON a.cluster_id = c.id AND a.lifecycle = 'active'
                            WHERE c.telemetry_location = 'cluster' AND c.lifecycle = 'active'
                            GROUP BY c.id, c.cluster_ref
                            """
                        ),
                        {"fresh": HEARTBEAT_FRESH_SECONDS},
                    )
                ).all()
            self._cached = {
                str(row[1]): RelayedCluster(id=row[0], cluster_ref=str(row[1]), online=bool(row[2]))
                for row in rows
            }
            self._cached_at = time.monotonic()
            return self._cached

    async def call(
        self, cluster_ref: str, kind: str, request: dict[str, Any], *, timeout_seconds: float
    ) -> RelayReply:
        if kind not in KINDS:
            raise ValueError(f"unknown relay kind: {kind!r}")
        target = (await self.clusters()).get(cluster_ref)
        if target is None:
            raise RelayUnavailableError("store_not_configured")
        if not target.online:
            raise RelayUnavailableError("store_agent_offline")

        request_id = uuid.uuid4().hex
        deadline = time.time() + timeout_seconds
        item = json.dumps(
            {"id": request_id, "kind": kind, "deadline": deadline, "request": request},
            separators=(",", ":"),
        )
        redis = self._client()
        ttl = int(timeout_seconds) + 30
        async with redis.pipeline(transaction=True) as pipe:
            pipe.set(owner_key(request_id), str(target.id), ex=ttl)
            pipe.rpush(queue_key(target.id), item)
            pipe.expire(queue_key(target.id), _QUEUE_TTL_SECONDS)
            await pipe.execute()

        popped = await redis.blpop([reply_key(request_id)], timeout=max(0.1, timeout_seconds))
        if popped is None:
            # The agent may still answer; the listener will find no owner
            # and drop it.
            await redis.delete(owner_key(request_id))
            raise RelayUnavailableError("store_timeout")
        return decode_reply(popped[1])


def decode_reply(raw: bytes | str) -> RelayReply:
    try:
        reply = json.loads(raw)
    except ValueError as error:
        raise RelayUnavailableError("store_unreachable") from error
    if not isinstance(reply, dict):
        raise RelayUnavailableError("store_unreachable")
    failure = reply.get("error")
    if failure:
        raise RelayUnavailableError(_AGENT_ERRORS.get(str(failure), "store_unreachable"))
    status = reply.get("status_code")
    if not isinstance(status, int):
        raise RelayUnavailableError("store_unreachable")
    try:
        body = base64.b64decode(str(reply.get("body_b64") or ""), validate=True)
    except ValueError as error:
        raise RelayUnavailableError("store_unreachable") from error
    return RelayReply(
        status_code=status, content_type=str(reply.get("content_type") or ""), body=body
    )
