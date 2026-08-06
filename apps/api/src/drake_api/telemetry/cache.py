"""Bounded normalized response cache + last-good envelopes (Redis).

Only normalized, already-safe envelopes are stored — never raw PromQL,
provider URLs, config refs, credentials, raw provider responses/errors, or
principal material. Fresh entries are keyed on the exact aligned time
window; last-good entries drop the window (keeping range length + step) so
a provider outage can fall back to the most recent successful envelope for
the same logical query.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis

_FORBIDDEN_SUBSTRINGS = ("config_ref", "provider_url", "authorization", "bearer ")
_MAX_ENVELOPE_BYTES = 512 * 1024


@dataclass(frozen=True)
class CacheKeys:
    fresh: str
    last_good: str


def build_cache_keys(
    *,
    registry_hash: str,
    template_key: str,
    template_version: int,
    integration_identity: str,
    scope_type: str,
    scope_id: str,
    matcher_set: tuple[str, ...],
    parameters: dict[str, str],
    from_ts: int,
    to_ts: int,
    effective_step_seconds: int,
) -> CacheKeys:
    base = {
        "registry": registry_hash,
        "template": f"{template_key}@{template_version}",
        "integration": integration_identity,
        "scope": f"{scope_type}:{scope_id}",
        "matchers": list(matcher_set),
        "parameters": dict(sorted(parameters.items())),
        "step": effective_step_seconds,
    }
    window = {**base, "from": from_ts, "to": to_ts}
    logical = {**base, "range_seconds": to_ts - from_ts}
    fresh = hashlib.sha256(json.dumps(window, sort_keys=True).encode()).hexdigest()
    last_good = hashlib.sha256(json.dumps(logical, sort_keys=True).encode()).hexdigest()
    return CacheKeys(fresh=f"telemetry:fresh:{fresh}", last_good=f"telemetry:lastgood:{last_good}")


def assert_envelope_safe(envelope: dict[str, Any]) -> None:
    """Last line of defense: refuse to cache anything provider-shaped."""
    serialized = json.dumps(envelope, sort_keys=True)
    lowered = serialized.lower()
    for needle in _FORBIDDEN_SUBSTRINGS:
        if needle in lowered:
            raise ValueError(f"unsafe envelope content: {needle!r}")
    if len(serialized) > _MAX_ENVELOPE_BYTES:
        raise ValueError("envelope exceeds the cache size bound")


class TelemetryCache:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(key)
        except Exception:
            return None  # cache is an optimization; authorization already ran
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    async def put(
        self, keys: CacheKeys, envelope: dict[str, Any], *, fresh_ttl: int, stale_ttl: int
    ) -> None:
        assert_envelope_safe(envelope)
        serialized = json.dumps(envelope, sort_keys=True)
        try:
            await self._redis.set(keys.fresh, serialized, ex=fresh_ttl)
            await self._redis.set(keys.last_good, serialized, ex=stale_ttl)
        except Exception:  # noqa: S110 - cache writes are best-effort
            pass
