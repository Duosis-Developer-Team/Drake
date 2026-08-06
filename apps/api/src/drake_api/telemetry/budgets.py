"""Query budgets: range/step/point math and Redis concurrency leases.

The lease layer is fail-closed: if Redis is unavailable the query is
refused with a typed retryable error — budgets are never bypassed.
"""

import asyncio
import math
import uuid
from dataclasses import dataclass
from datetime import datetime

import redis.asyncio as aioredis

from drake_api.telemetry.registry import GLOBAL_MAX_RANGE_SECONDS, QueryTemplate

PRINCIPAL_CONCURRENCY = 4
TARGET_CONCURRENCY = 8
_LEASE_TTL_SECONDS = 30


class BudgetError(ValueError):
    """The requested range/step violates a budget (422)."""


class BudgetUnavailableError(RuntimeError):
    """The Redis budget layer is unavailable (typed retryable 503)."""


class ConcurrencyRejectedError(RuntimeError):
    """The concurrency budget is exhausted (typed retryable 429)."""


@dataclass(frozen=True)
class EffectiveRange:
    from_ts: int
    to_ts: int
    requested_step_seconds: int
    effective_step_seconds: int
    step_adjusted: bool


def resolve_range(
    template: QueryTemplate, from_dt: datetime, to_dt: datetime, step_seconds: int
) -> EffectiveRange:
    from_ts, to_ts = int(from_dt.timestamp()), int(to_dt.timestamp())
    if to_ts <= from_ts:
        raise BudgetError("range end must be after range start")
    range_seconds = to_ts - from_ts
    max_range = min(template.max_range_seconds, GLOBAL_MAX_RANGE_SECONDS)
    if range_seconds > max_range:
        raise BudgetError("requested range exceeds the template budget")

    # Server-side step adjustment: a tiny step can never mint an unbounded
    # query — it is raised to the minimum step, then to the point budget.
    effective = max(step_seconds, template.min_step_seconds)
    if math.ceil(range_seconds / effective) + 1 > template.max_points:
        effective = math.ceil(range_seconds / (template.max_points - 1))
    return EffectiveRange(
        from_ts=from_ts,
        to_ts=to_ts,
        requested_step_seconds=step_seconds,
        effective_step_seconds=effective,
        step_adjusted=effective != step_seconds,
    )


# Atomic lease acquire: drop expired tokens, then add ours only when the
# bucket has room. KEYS[1]=zset, ARGV=[token, now_ms, ttl_ms, limit].
_ACQUIRE = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[2])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[4]) then
  return 0
end
redis.call('ZADD', KEYS[1], tonumber(ARGV[2]) + tonumber(ARGV[3]), ARGV[1])
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return 1
"""


class ConcurrencyLeases:
    """Redis-backed concurrency leases with unique tokens and stale recovery.

    Tokens live in a sorted set scored by their expiry; every acquire first
    sweeps expired tokens, so leases leaked by a crashed process recover
    within the bounded TTL. Release removes only the caller's own token.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def acquire(self, keys: list[str], now_ms: int) -> list[tuple[str, str]]:
        held: list[tuple[str, str]] = []
        try:
            for key, limit in keys_with_limits(keys):
                token = uuid.uuid4().hex
                granted = await self._redis.eval(
                    _ACQUIRE, 1, key, token, str(now_ms), str(_LEASE_TTL_SECONDS * 1000), str(limit)
                )
                if not int(granted):
                    await self._release(held)
                    raise ConcurrencyRejectedError("concurrent query budget exhausted")
                held.append((key, token))
        except ConcurrencyRejectedError:
            raise
        except asyncio.CancelledError:
            # Client disconnected mid-acquire: release any partially held
            # tokens (own tokens only), then let the cancellation propagate.
            # The bounded lease TTL remains the backstop if Redis fails here.
            await self._release(held)
            raise
        except Exception as error:
            # Redis down mid-acquire: clean up best-effort, then FAIL CLOSED.
            await self._release(held)
            raise BudgetUnavailableError("budget layer unavailable") from error
        return held

    async def release(self, held: list[tuple[str, str]]) -> None:
        await self._release(held)

    async def _release(self, held: list[tuple[str, str]]) -> None:
        for key, token in held:
            try:
                await self._redis.zrem(key, token)
            except Exception:  # noqa: S110 - leases self-expire via TTL
                pass


def keys_with_limits(keys: list[str]) -> list[tuple[str, int]]:
    resolved = []
    for key in keys:
        limit = (
            PRINCIPAL_CONCURRENCY
            if key.startswith("telemetry:lease:principal:")
            else (TARGET_CONCURRENCY)
        )
        resolved.append((key, limit))
    return resolved


def principal_lease_key(principal_id: str) -> str:
    return f"telemetry:lease:principal:{principal_id}"


def target_lease_key(scope_id: str) -> str:
    return f"telemetry:lease:target:{scope_id}"
