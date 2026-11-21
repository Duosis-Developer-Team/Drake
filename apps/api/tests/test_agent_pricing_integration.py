"""Agent cost against the real database (Agent Visibility, phase 4): a
price list fetched, versioned, matched to the models agents use, and the
cost the inventory shows."""

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from drake_api.ai_agents import pricing
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_agents_integration import NOW, redis, seed
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db", "redis"]

SOURCE = "https://prices.example/model_prices.json"
DOC = {
    "claude-sonnet-5": {
        "litellm_provider": "anthropic",
        "mode": "chat",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
        "cache_read_input_token_cost": 3e-07,
    },
    "gpt-5-mini": {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 2.5e-07,
        "output_cost_per_token": 2e-06,
    },
}
# ticket-triager: 1200 in (800 cached), 300 out (40 reasoning) on claude-sonnet-5.
TRIAGER = (400 * 3 + 800 * 0.3 + 260 * 15 + 40 * 15) / 1e6
# batch (eBPF only): 700 in, 70 out on gpt-5-mini.
BATCH = (700 * 0.25 + 70 * 2) / 1e6


async def clear_prices(engine: AsyncEngine, redis: Any) -> None:
    await redis.delete(pricing.LEASE_KEY)
    async with engine.begin() as connection:
        for table in ("model_price_matches", "model_prices", "model_price_fetches"):
            await connection.execute(text(f"DELETE FROM {table}"))  # noqa: S608


def serving(document: Any, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(status, content=json.dumps(document).encode())
    )


async def test_costs_follow_the_price_in_force_each_hour(engine: AsyncEngine, redis: Any) -> None:
    await seed(engine, redis)
    await clear_prices(engine, redis)
    report = await pricing.run_once(
        engine, redis, source=SOURCE, refresh=timedelta(hours=24), now=NOW, transport=serving(DOC)
    )
    assert (report.fetched, report.changed) == ("live", 2)
    assert report.matched >= 3
    async with engine.connect() as connection:
        matches = {
            str(r[1]): r[2]
            for r in (
                await connection.execute(
                    text("SELECT provider, model, model_key FROM model_price_matches")
                )
            ).all()
        }
    assert matches["claude-sonnet-5"] == "claude-sonnet-5"
    assert matches["gpt-5-mini"] == "gpt-5-mini"
    assert matches["gpt-5"] is None  # not in this list: no price, not a guess

    harness = build_harness(require_it_settings())
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        body = (await client.get("/v1/agents?window=24h")).json()
        by_name = {a["display_name"]: a for a in body["items"]}
        assert by_name["ticket-triager"]["cost_usd"] == pytest.approx(TRIAGER, abs=1e-6)
        assert by_name["batch"]["cost_usd"] == pytest.approx(BATCH, abs=1e-6)
        assert by_name["research-helper"]["cost_usd"] == 0
        assert by_name["research-helper"]["unpriced_tokens"] == 99
        assert body["summary"]["cost_usd"] == pytest.approx(TRIAGER + BATCH, abs=1e-6)
        assert sum(p["cost_usd"] for p in body["summary"]["series"]) == pytest.approx(
            TRIAGER + BATCH, abs=1e-6
        )
        detail = (await client.get(f"/v1/agents/{by_name['ticket-triager']['id']}")).json()
        assert detail["models"][0]["cost_usd"] == pytest.approx(TRIAGER, abs=1e-6)
        status = (await client.get("/v1/agents/prices")).json()
        assert status["last_fetch"]["status"] == "live"
        assert status["priced_models"] == 2
        assert {"provider": "openai", "model": "gpt-5"} in status["unpriced"]

        # The price doubles after the hour was used: that hour keeps its price.
        dearer = {
            **DOC,
            "claude-sonnet-5": {**DOC["claude-sonnet-5"], "input_cost_per_token": 6e-06},
        }
        _, changed = await pricing.apply(
            engine,
            pricing.parse_litellm(dearer),
            source=SOURCE,
            status="live",
            sha256=None,
            now=NOW + timedelta(minutes=1),
        )
        assert changed == 1
        again = (await client.get("/v1/agents?window=24h")).json()
        triager = next(a for a in again["items"] if a["display_name"] == "ticket-triager")
        assert triager["cost_usd"] == pytest.approx(TRIAGER, abs=1e-6)

    async with engine.connect() as connection:
        history = (
            await connection.execute(
                text(
                    "SELECT input_usd, valid_to IS NULL FROM model_prices "
                    "WHERE model_key = 'claude-sonnet-5' ORDER BY valid_from"
                )
            )
        ).all()
    assert [(r[0], r[1]) for r in history] == [
        (Decimal("3.000000"), False),
        (Decimal("6.000000"), True),
    ]
    # The same list again changes nothing.
    _, unchanged = await pricing.apply(
        engine, pricing.parse_litellm(dearer), source=SOURCE, status="live", sha256=None, now=NOW
    )
    assert unchanged == 0


async def test_an_unreachable_list_falls_back_to_the_bundled_one(
    engine: AsyncEngine, redis: Any
) -> None:
    await seed(engine, redis)
    await clear_prices(engine, redis)
    report = await pricing.run_once(
        engine,
        redis,
        source=SOURCE,
        refresh=timedelta(hours=24),
        now=NOW,
        transport=serving({}, status=503),
    )
    assert report.fetched == "fallback"
    async with engine.connect() as connection:
        statuses = [
            r[0]
            for r in (
                await connection.execute(text("SELECT status FROM model_price_fetches ORDER BY id"))
            ).all()
        ]
        priced = (
            await connection.execute(
                text("SELECT count(*) FROM model_prices WHERE valid_to IS NULL")
            )
        ).scalar_one()
    assert statuses == ["failed", "fallback"]
    assert priced > 500
    # Not due again within the hour: no second attempt, no second fallback.
    again = await pricing.run_once(
        engine,
        redis,
        source=SOURCE,
        refresh=timedelta(hours=24),
        now=NOW + timedelta(minutes=10),
        transport=serving({}, status=503),
    )
    assert again.fetched == ""


async def test_the_source_must_be_https() -> None:
    with pytest.raises(ValueError):
        await pricing.download("http://prices.example/list.json")
