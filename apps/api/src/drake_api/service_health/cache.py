"""Computed-health cache, with a last-good fallback.

The Query Broker already caches each individual template response. This
layer caches the *verdict*, because a service list renders health for many
services at once and each verdict costs up to nine provider round-trips.

Two properties matter more than the hit rate:

**Invalidation is by identity, not by deletion.** Everything a verdict
depends on — the binding's revision, which workload it resolved to, the
preset, the policy, the datasource configuration, the registry content —
is hashed into the key. Mutating a binding bumps its revision, so the next
read computes a different key and *cannot* reach the pre-mutation entry.
There is no window in which a stale verdict is still addressable, and no
delete that can be missed or lost.

**A failure never destroys the last good answer.** Writes happen only
after a live computation succeeds. A provider outage reads the last-good
entry and serves it explicitly marked stale, with the timestamp it was
actually computed at — never restamped as if it were current.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis

from drake_api.service_health.policy import HealthStatus

# A verdict may be reused for this long before it is recomputed. Short
# enough that an operator watching a rollout sees it move; long enough that
# a list of thirty services is not thirty times the provider load.
FRESH_TTL_SECONDS = 30
# How long a last-good verdict remains servable during an outage. Past this
# the honest answer is `unknown`, not an hour-old "healthy".
LAST_GOOD_TTL_SECONDS = 900

_FORBIDDEN_SUBSTRINGS = ("config_ref", "provider_url", "authorization", "bearer ", "promql")
_MAX_PAYLOAD_BYTES = 128 * 1024


@dataclass(frozen=True)
class HealthCacheKeys:
    fresh: str
    last_good: str


def build_health_cache_keys(
    *,
    registry_hash: str,
    binding_id: str,
    revision: int,
    resolved_resource_uid: str | None,
    preset_key: str,
    policy_key: str,
    datasource_identity: str,
    project_key: str,
    environment_key: str,
    service_key: str,
    window_seconds: int,
    step_seconds: int,
) -> HealthCacheKeys:
    """One identity for one computable verdict.

    `project/environment/service` are in the key even though the binding id
    already implies them: it keeps entries from two environments of the
    same service provably distinct rather than distinct-by-argument, and it
    makes a mistaken join visible as a cache miss instead of a wrong answer.
    """
    identity = {
        "registry": registry_hash,
        # Revision and resolved uid are what make a mutation unreachable:
        # lifecycle/preset/policy edits bump revision, and re-resolution
        # changes the uid.
        "binding": f"{binding_id}@{revision}",
        "resolved": resolved_resource_uid or "",
        "preset": preset_key,
        "policy": policy_key,
        # Reconfiguring the datasource invalidates every verdict read
        # through it. The ref itself is hashed by the caller and never
        # stored here.
        "datasource": datasource_identity,
        "catalog": f"{project_key}/{environment_key}/{service_key}",
        "window": window_seconds,
        "step": step_seconds,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return HealthCacheKeys(
        fresh=f"service_health:fresh:{digest}",
        last_good=f"service_health:lastgood:{digest}",
    )


def datasource_identity(integration_id: str | None, config_ref: str | None, state: str) -> str:
    """A stable, non-reversible name for "which datasource, configured how".

    The config ref is hashed and immediately discarded; it never reaches
    Redis, a log line, or a response.
    """
    if integration_id is None:
        return "none"
    return hashlib.sha256(f"{integration_id}:{config_ref or ''}:{state}".encode()).hexdigest()


def assert_payload_safe(payload: dict[str, Any]) -> None:
    """Refuse to cache anything provider-shaped. Same rule as the broker."""
    serialized = json.dumps(payload, sort_keys=True)
    lowered = serialized.lower()
    for needle in _FORBIDDEN_SUBSTRINGS:
        if needle in lowered:
            raise ValueError(f"unsafe health payload content: {needle!r}")
    if len(serialized) > _MAX_PAYLOAD_BYTES:
        raise ValueError("health payload exceeds the cache size bound")


class HealthCache:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(key)
        except Exception:
            # The cache is an optimization. Redis being down means a slower
            # answer, never a wrong one — and never an unauthorized one,
            # since authorization ran before we got here.
            return None
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    async def put(
        self, keys: HealthCacheKeys, payload: dict[str, Any], *, last_good: bool = True
    ) -> None:
        """Store a verdict.

        `last_good=False` for a verdict that was itself computed from stale
        telemetry: it is worth reusing for a few seconds, but promoting it
        to the fallback would let staleness compound — an outage would then
        serve an answer whose `computed_at` is recent and whose evidence is
        not. Nothing here deletes the last-good entry either, so a failure
        always leaves something honest to fall back to.
        """
        assert_payload_safe(payload)
        serialized = json.dumps(payload, sort_keys=True)
        try:
            await self._redis.set(keys.fresh, serialized, ex=FRESH_TTL_SECONDS)
            if last_good:
                await self._redis.set(keys.last_good, serialized, ex=LAST_GOOD_TTL_SECONDS)
        except Exception:  # noqa: S110 - cache writes are best-effort
            pass

    async def drop(self, key: str) -> None:
        """Forget one fresh verdict.

        Only ever called with a `fresh` key. Last-good entries are never
        deleted here: the whole point of keeping them is that they outlive
        whatever went wrong.
        """
        try:
            await self._redis.delete(key)
        except Exception:  # noqa: S110 - TTL bounds the damage if this fails
            pass


# Statuses that stale evidence cannot support. A workload that was failing
# when last observed is not improved by the observation being old, so a bad
# verdict survives the downgrade and a good one does not.
_DOWNGRADED = frozenset({HealthStatus.HEALTHY, HealthStatus.UNKNOWN, HealthStatus.NOT_CONFIGURED})


def as_last_good(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Present a cached verdict as what it is: an old one.

    `computed_at` keeps its original value — the moment the answer was
    actually true. `served_at` and `age_seconds` say how long ago that was.
    Restamping `computed_at` to now would be the one dishonest thing this
    whole layer exists to avoid.
    """
    served = dict(payload)
    status = str(served.get("status", HealthStatus.UNKNOWN))
    if status in _DOWNGRADED:
        served["status"] = str(HealthStatus.STALE)

    reasons = list(served.get("reasons", []))
    if "telemetry_stale" not in reasons:
        reasons.append("telemetry_stale")
    served["reasons"] = reasons

    messages = list(served.get("messages", []))
    stale_text = "Telemetry is older than this policy allows."
    if stale_text not in messages:
        messages.append(stale_text)
    served["messages"] = messages

    computed_at = served.get("computed_at")
    age: float | None = None
    if isinstance(computed_at, str):
        try:
            age = (now - datetime.fromisoformat(computed_at)).total_seconds()
        except ValueError:
            age = None

    served["partial"] = True
    served["served_from_last_good"] = True
    served["served_at"] = now.astimezone(UTC).isoformat()
    served["age_seconds"] = age
    return served
