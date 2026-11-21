"""What agents' tokens cost: model prices, kept current and versioned in time.

Prices change — providers cut them, add cache tiers, release models — so
they are not written into code. A public price list (LiteLLM's model price
table by default) is fetched once a day; each model's price is kept with
the time it took effect, and a changed price closes the old one. An hour
of usage is costed at the price in force in that hour. If the list cannot
be fetched and nothing was ever loaded, a snapshot bundled with Tracesis
is loaded instead (and says so).

Telemetry names models its own way ("claude-sonnet-4-5-20250929",
"us.anthropic.claude-...", "gpt-5-mini"); each (provider, model) seen is
matched to a price key once and the match is kept, with how it was made:
exact, by provider prefix, by bare name, or after dropping a date/version
suffix — or not at all, which the screens show as "no price".

Costs are estimates at list prices (no discounts, batch or priority
tiers), in US dollars.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import resources
from typing import Any

import httpx
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
MAX_BYTES = 25 * 1024 * 1024
LEASE_KEY = "agents:prices:cycle"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MODES = frozenset({"chat", "completion", "responses", "embedding"})
MILLION = Decimal(1_000_000)

# Our provider names (OpenTelemetry `gen_ai.provider.name`) → the price
# list's key prefixes and provider names.
_PROVIDERS: dict[str, tuple[str, ...]] = {
    "openai": ("openai",),
    "anthropic": ("anthropic",),
    "gcp.gemini": ("gemini",),
    "gemini": ("gemini",),
    "gcp.vertex_ai": ("vertex_ai", "vertex_ai-language-models", "vertex_ai-anthropic_models"),
    "aws.bedrock": ("bedrock", "bedrock_converse"),
    "azure.ai.openai": ("azure",),
    "azure": ("azure",),
    "mistral_ai": ("mistral",),
    "mistral": ("mistral",),
    "x_ai": ("xai",),
    "xai": ("xai",),
    "deepseek": ("deepseek",),
    "groq": ("groq",),
    "cohere": ("cohere", "cohere_chat"),
    "perplexity": ("perplexity",),
    # Self-hosted: the list's entries are free; a reseller's price is not ours.
    "ollama": ("ollama", "ollama_chat"),
    "vllm": ("hosted_vllm",),
}
_REGION = re.compile(r"^(us|eu|apac|global|jp|au|ca)\.")
# What may follow a model's name and still be the same model: a date, a
# preview/latest/experimental tag or a version — never "-mini" or "-pro".
_RELEASE_TAIL = re.compile(
    r"^(\d{4}(-\d{2}-\d{2})?|\d{6,8}|(preview|latest|exp)(-[\d-]+)?|v\d+(:\d+)?)$"
)
_SUFFIX = re.compile(r"(-\d{8}|-\d{4}-\d{2}-\d{2}|@\d{8}|-v\d+(:\d+)?|:\d+|-latest|-preview)$")


def infer_provider(model: str) -> str:
    """A provider for a model named without one (some SDKs and eBPF set
    only the model). Empty when the name does not say."""
    m = model.strip().lower()
    if _REGION.sub("", m).startswith(
        ("anthropic.", "amazon.", "meta.llama", "cohere.", "mistral.", "ai21.", "deepseek.")
    ):
        return "aws.bedrock"
    if m.startswith(("gpt-", "gpt4", "o1", "o3", "o4", "chatgpt", "text-embedding", "davinci")):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gemini", "gemma")):
        return "gcp.gemini"
    if m.startswith(("mistral", "mixtral", "codestral", "magistral", "ministral", "pixtral")):
        return "mistral_ai"
    if m.startswith("grok"):
        return "x_ai"
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith("command"):
        return "cohere"
    return ""


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens."""

    key: str
    provider: str
    mode: str
    input: Decimal
    output: Decimal
    cache_read: Decimal | None = None
    cache_write: Decimal | None = None
    reasoning: Decimal | None = None

    def values(self) -> tuple[Decimal | None, ...]:
        return (self.input, self.output, self.cache_read, self.cache_write, self.reasoning)


def _per_million(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return (Decimal(str(value)) * MILLION).quantize(Decimal("0.000001"))


def parse_litellm(document: Any) -> dict[str, Price]:
    """The token prices of text models in a LiteLLM price document."""
    if not isinstance(document, dict):
        raise ValueError("price document is not an object")
    prices: dict[str, Price] = {}
    for raw_key, entry in document.items():
        if not isinstance(entry, dict) or raw_key == "sample_spec":
            continue
        mode = str(entry.get("mode") or "chat")
        if mode not in MODES:
            continue
        key = str(raw_key).strip().lower()
        if not key or len(key) > 256:
            continue
        inp = _per_million(entry.get("input_cost_per_token"))
        out = _per_million(entry.get("output_cost_per_token"))
        if inp is None or (out is None and mode != "embedding"):
            continue
        prices[key] = Price(
            key=key,
            provider=str(entry.get("litellm_provider") or "")[:64],
            mode=mode,
            input=inp,
            output=out if out is not None else Decimal(0),
            cache_read=_per_million(entry.get("cache_read_input_token_cost")),
            cache_write=_per_million(entry.get("cache_creation_input_token_cost")),
            reasoning=_per_million(entry.get("output_cost_per_reasoning_token")),
        )
    if not prices:
        raise ValueError("price document has no model prices")
    return prices


class PriceIndex:
    """Matches telemetry model names to price keys."""

    def __init__(self, providers: dict[str, str]) -> None:
        # key → the price list's provider name
        self.providers = providers
        self.bare: dict[str, list[str]] = {}
        for key in providers:
            self.bare.setdefault(key.rsplit("/", 1)[-1], []).append(key)

    def _pick(self, keys: list[str], provider: str) -> str:
        wanted = _PROVIDERS.get(provider, ())

        def rank(key: str) -> tuple[int, int, int, str]:
            same = 0 if self.providers.get(key, "") in wanted else 1
            return (same, key.count("/"), len(key), key)

        return sorted(keys, key=rank)[0]

    def _try(self, name: str, provider: str) -> tuple[str | None, str]:
        if name in self.providers:
            return name, "exact"
        wanted = _PROVIDERS.get(provider, ())
        for prefix in wanted:
            if f"{prefix}/{name}" in self.providers:
                return f"{prefix}/{name}", "provider"
        keys = self.bare.get(name, [])
        # A bare name is trusted only from the model's own provider: the same
        # name under a reseller or gateway is a different price.
        own = [k for k in keys if self.providers.get(k, "") in wanted]
        if own:
            return self._pick(own, provider), "bare"
        if not wanted and keys:
            first_party = [k for k in keys if "/" not in k]
            if first_party:
                return self._pick(first_party, provider), "bare"
        # A dated or preview release of the same model from the same provider
        # ("gemini-3-pro" → "gemini-3-pro-preview", "grok-4" → "grok-4-0709").
        if wanted:
            longer = [
                k
                for bare, ks in self.bare.items()
                if bare.startswith(f"{name}-") and _RELEASE_TAIL.match(bare[len(name) + 1 :])
                for k in ks
                if self.providers.get(k, "") in wanted
            ]
            if longer:
                return sorted(longer, key=lambda k: (len(k), k))[0], "release"
        return None, "none"

    def match(self, provider: str, model: str) -> tuple[str | None, str]:
        name = model.strip().lower()
        if not name:
            return None, "none"
        if "/" in name:  # "openai/gpt-5" as some SDKs send it
            key, how = self._try(name, provider)
            if key:
                return key, how
            name = name.rsplit("/", 1)[-1]
        seen: set[str] = set()
        variants = [(name, "")]
        stripped = name
        while True:
            shorter = _SUFFIX.sub("", stripped)
            if shorter == stripped or not shorter:
                break
            stripped = shorter
            variants.append((stripped, "normalized"))
        for variant, _label in list(variants):
            regional = _REGION.sub("", variant)
            if regional != variant:
                variants.append((regional, "normalized"))
        for variant, label in variants:
            if variant in seen:
                continue
            seen.add(variant)
            key, how = self._try(variant, provider)
            if key:
                return key, label or how
        return None, "none"


def fallback_document() -> dict[str, Any]:
    """The price snapshot bundled with Tracesis (a trimmed LiteLLM table)."""
    data = resources.files("drake_api.ai_agents").joinpath("prices_fallback.json").read_text()
    document: dict[str, Any] = json.loads(data)
    return document


async def download(url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("the price source must be https")
    async with (
        httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "tracesis-prices"},
        ) as client,
        client.stream("GET", url) as response,
    ):
        if response.status_code != 200:
            raise ValueError(f"price source answered {response.status_code}")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError("price document too large")
    return bytes(body)


async def apply(
    engine: AsyncEngine,
    prices: dict[str, Price],
    *,
    source: str,
    status: str,
    sha256: str | None,
    now: datetime,
) -> tuple[int, int]:
    """Record a fetch and bring current prices in line with it. A new model
    takes effect from the beginning (its first known price is the best
    estimate for hours already seen); a changed price closes the old row
    and opens a new one now. Models missing from the list keep their last
    price. Returns (fetch id, prices changed or added)."""
    async with engine.begin() as connection:
        current = {
            str(r[0]): (r[1], r[2], r[3], r[4], r[5])
            for r in (
                await connection.execute(
                    text(
                        "SELECT model_key, input_usd, output_usd, cache_read_usd, "
                        "cache_write_usd, reasoning_usd FROM model_prices WHERE valid_to IS NULL"
                    )
                )
            ).all()
        }
        fetch_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO model_price_fetches (fetched_at, source, status, sha256, models)
                    VALUES (:now, :source, :status, :sha, :models) RETURNING id
                    """
                ),
                {
                    "now": now,
                    "source": source[:512],
                    "status": status,
                    "sha": sha256,
                    "models": len(prices),
                },
            )
        ).scalar_one()
        added, changed = [], []
        for price in prices.values():
            old = current.get(price.key)
            if old is None:
                added.append(price)
            elif tuple(old) != price.values():
                changed.append(price)
        if changed:
            await connection.execute(
                text(
                    "UPDATE model_prices SET valid_to = :now "
                    "WHERE valid_to IS NULL AND model_key = ANY(:keys)"
                ),
                {"now": now, "keys": [p.key for p in changed]},
            )
        rows = [(p, EPOCH) for p in added] + [(p, now) for p in changed]
        if rows:
            await connection.execute(
                text(
                    """
                    INSERT INTO model_prices
                        (model_key, provider, mode, input_usd, output_usd, cache_read_usd,
                         cache_write_usd, reasoning_usd, valid_from, fetch_id)
                    VALUES (:key, :provider, :mode, :input, :output, :cache_read,
                            :cache_write, :reasoning, :from, :fetch)
                    """
                ),
                [
                    {
                        "key": p.key,
                        "provider": p.provider,
                        "mode": p.mode,
                        "input": p.input,
                        "output": p.output,
                        "cache_read": p.cache_read,
                        "cache_write": p.cache_write,
                        "reasoning": p.reasoning,
                        "from": start,
                        "fetch": fetch_id,
                    }
                    for p, start in rows
                ],
            )
        await connection.execute(
            text("UPDATE model_price_fetches SET changed = :n WHERE id = :id"),
            {"n": len(rows), "id": fetch_id},
        )
    return int(fetch_id), len(rows)


async def record_failure(engine: AsyncEngine, *, source: str, error: str, now: datetime) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO model_price_fetches (fetched_at, source, status, error)
                VALUES (:now, :source, 'failed', :error)
                """
            ),
            {"now": now, "source": source[:512], "error": error[:200]},
        )


async def resolve(engine: AsyncEngine, *, now: datetime, everything: bool = False) -> int:
    """Match the (provider, model) pairs seen in agent usage to price keys:
    the new ones, or all of them after the prices changed."""
    async with engine.begin() as connection:
        providers = {
            str(r[0]): str(r[1])
            for r in (
                await connection.execute(
                    text("SELECT model_key, provider FROM model_prices WHERE valid_to IS NULL")
                )
            ).all()
        }
        if not providers:
            return 0
        pairs = (
            await connection.execute(
                text(
                    """
                    SELECT DISTINCT r.provider, r.model FROM agent_rollups r
                    WHERE r.model <> ''
                      AND (:everything OR NOT EXISTS (
                          SELECT 1 FROM model_price_matches m
                          WHERE m.provider = r.provider AND m.model = r.model))
                    LIMIT 5000
                    """
                ),
                {"everything": everything},
            )
        ).all()
        if not pairs:
            return 0
        index = PriceIndex(providers)
        rows = []
        for provider, model in pairs:
            key, how = index.match(str(provider), str(model))
            rows.append(
                {
                    "provider": provider,
                    "model": model,
                    "key": key,
                    "how": how,
                    "now": now,
                }
            )
        await connection.execute(
            text(
                """
                INSERT INTO model_price_matches (provider, model, model_key, matched_by,
                                                 resolved_at)
                VALUES (:provider, :model, :key, :how, :now)
                ON CONFLICT (provider, model) DO UPDATE SET
                    model_key = EXCLUDED.model_key, matched_by = EXCLUDED.matched_by,
                    resolved_at = EXCLUDED.resolved_at
                """
            ),
            rows,
        )
    return len(rows)


_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass
class PriceReport:
    lease_acquired: bool = False
    fetched: str = ""  # live | fallback | failed | "" (not due)
    changed: int = 0
    matched: int = 0


async def run_once(
    engine: AsyncEngine,
    redis: aioredis.Redis,
    *,
    source: str,
    refresh: timedelta,
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PriceReport:
    moment = now or datetime.now(UTC)
    report = PriceReport()
    token = uuid.uuid4().hex
    if not await redis.set(LEASE_KEY, token, nx=True, ex=300):
        return report
    report.lease_acquired = True
    try:
        async with engine.connect() as connection:
            last = (
                await connection.execute(
                    text(
                        "SELECT max(fetched_at) FROM model_price_fetches "
                        "WHERE status IN ('live', 'fallback')"
                    )
                )
            ).scalar_one_or_none()
            last_try = (
                await connection.execute(text("SELECT max(fetched_at) FROM model_price_fetches"))
            ).scalar_one_or_none()
            have = (
                await connection.execute(
                    text("SELECT EXISTS (SELECT 1 FROM model_prices WHERE valid_to IS NULL)")
                )
            ).scalar_one()
        # A failed attempt is retried after an hour, not every cycle.
        due = (last is None or moment - last >= refresh) and (
            last_try is None or moment - last_try >= timedelta(hours=1) or last is None
        )
        if due:
            try:
                body = await download(source, transport=transport)
                prices = parse_litellm(json.loads(body))
                _, report.changed = await apply(
                    engine,
                    prices,
                    source=source,
                    status="live",
                    sha256=hashlib.sha256(body).hexdigest(),
                    now=moment,
                )
                report.fetched = "live"
            except Exception as error:
                logger.warning("model prices: fetch failed", extra={"code": type(error).__name__})
                await record_failure(engine, source=source, error=type(error).__name__, now=moment)
                report.fetched = "failed"
                if not have:
                    _, report.changed = await apply(
                        engine,
                        parse_litellm(fallback_document()),
                        source="bundled snapshot",
                        status="fallback",
                        sha256=None,
                        now=moment,
                    )
                    report.fetched = "fallback"
        report.matched = await resolve(engine, now=moment, everything=report.changed > 0)
    finally:
        await redis.eval(_RELEASE, 1, LEASE_KEY, token)
    return report


class PriceKeeper:
    """Keeps prices current and usage matched to them, every few minutes."""

    def __init__(
        self,
        engine: AsyncEngine,
        redis: aioredis.Redis,
        *,
        source: str,
        refresh: timedelta,
        interval_seconds: float = 600.0,
    ) -> None:
        self._engine = engine
        self._redis = redis
        self._source = source
        self._refresh = refresh
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while True:
            try:
                await run_once(
                    self._engine, self._redis, source=self._source, refresh=self._refresh
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("model prices: a cycle failed")
            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="agent-prices")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
