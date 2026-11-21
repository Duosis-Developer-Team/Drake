"""Model prices (Agent Visibility, phase 4): reading the list, matching
telemetry's model names to it, and costing tokens."""

from decimal import Decimal

import pytest
from drake_api.ai_agents import pricing
from drake_api.ai_agents.repository import token_cost

DOC = {
    "sample_spec": {"input_cost_per_token": 0.0, "output_cost_per_token": 0.0},
    "claude-sonnet-5": {
        "litellm_provider": "anthropic",
        "mode": "chat",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
        "cache_read_input_token_cost": 3e-07,
        "cache_creation_input_token_cost": 3.75e-06,
    },
    "gpt-5-mini": {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 2.5e-07,
        "output_cost_per_token": 2e-06,
    },
    "gemini/gemini-2.5-pro": {
        "litellm_provider": "gemini",
        "mode": "chat",
        "input_cost_per_token": 1.25e-06,
        "output_cost_per_token": 1e-05,
    },
    "anthropic.claude-haiku-5": {
        "litellm_provider": "bedrock",
        "mode": "chat",
        "input_cost_per_token": 8e-07,
        "output_cost_per_token": 4e-06,
    },
    "xai/grok-9-0709": {
        "litellm_provider": "xai",
        "mode": "chat",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
    },
    "reseller/llama-5": {
        "litellm_provider": "reseller",
        "mode": "chat",
        "input_cost_per_token": 1e-07,
        "output_cost_per_token": 3e-07,
    },
    "dall-e-9": {
        "litellm_provider": "openai",
        "mode": "image_generation",
        "input_cost_per_token": 1,
    },
    "text-embedding-9": {
        "litellm_provider": "openai",
        "mode": "embedding",
        "input_cost_per_token": 2e-08,
    },
}


def test_the_list_is_read_per_million_tokens_and_text_models_only() -> None:
    prices = pricing.parse_litellm(DOC)
    assert set(prices) == {
        "claude-sonnet-5",
        "gpt-5-mini",
        "gemini/gemini-2.5-pro",
        "anthropic.claude-haiku-5",
        "xai/grok-9-0709",
        "reseller/llama-5",
        "text-embedding-9",
    }
    sonnet = prices["claude-sonnet-5"]
    assert (sonnet.input, sonnet.output) == (Decimal("3.000000"), Decimal("15.000000"))
    assert (sonnet.cache_read, sonnet.cache_write) == (Decimal("0.300000"), Decimal("3.750000"))
    assert prices["gpt-5-mini"].cache_read is None
    assert prices["text-embedding-9"].output == 0
    with pytest.raises(ValueError):
        pricing.parse_litellm({"sample_spec": {}})
    with pytest.raises(ValueError):
        pricing.parse_litellm([])


@pytest.mark.parametrize(
    ("provider", "model", "key", "how"),
    [
        ("anthropic", "claude-sonnet-5", "claude-sonnet-5", "exact"),
        ("anthropic", "Claude-Sonnet-5", "claude-sonnet-5", "exact"),
        ("gcp.gemini", "gemini-2.5-pro", "gemini/gemini-2.5-pro", "provider"),
        ("openai", "openai/gpt-5-mini", "gpt-5-mini", "exact"),
        ("anthropic", "claude-sonnet-5-20260101", "claude-sonnet-5", "normalized"),
        ("aws.bedrock", "us.anthropic.claude-haiku-5", "anthropic.claude-haiku-5", "normalized"),
        ("aws.bedrock", "anthropic.claude-haiku-5-v1:0", "anthropic.claude-haiku-5", "normalized"),
        ("x_ai", "grok-9", "xai/grok-9-0709", "release"),
        # A reseller's price for the same name is not the model's price.
        ("ollama", "llama-5", None, "none"),
        ("anthropic", "llama-5", None, "none"),
        ("openai", "gpt-unknown", None, "none"),
        # "-mini" is another model, not a release of this one.
        ("openai", "gpt-5", None, "none"),
        ("openai", "", None, "none"),
    ],
)
def test_models_match_their_own_providers_price(
    provider: str, model: str, key: str | None, how: str
) -> None:
    prices = pricing.parse_litellm(DOC)
    index = pricing.PriceIndex({k: v.provider for k, v in prices.items()})
    assert index.match(provider, model) == (key, how)


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("gpt-5", "openai"),
        ("o4-mini", "openai"),
        ("claude-opus-5", "anthropic"),
        ("gemini-3-pro", "gcp.gemini"),
        ("anthropic.claude-haiku-5", "aws.bedrock"),
        ("us.anthropic.claude-haiku-5", "aws.bedrock"),
        ("mistral-large-latest", "mistral_ai"),
        ("grok-4", "x_ai"),
        ("llama-5", ""),
    ],
)
def test_a_missing_provider_is_read_off_the_model_name(model: str, provider: str) -> None:
    assert pricing.infer_provider(model) == provider


def test_tokens_are_costed_by_kind() -> None:
    price = {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.3,
        "cache_write": 3.75,
        "reasoning": None,
    }
    # 1200 in of which 800 cached, 300 out of which 40 reasoning (at output price).
    cost = token_cost(price, inp=1200, out=300, cached=800, reasoning=40)
    assert cost == pytest.approx((400 * 3 + 800 * 0.3 + 260 * 15 + 40 * 15) / 1e6)
    no_cache_price = {**price, "cache_read": None}
    assert token_cost(no_cache_price, inp=100, out=0, cached=100, reasoning=0) == pytest.approx(
        100 * 3 / 1e6
    )


def test_the_bundled_snapshot_is_a_usable_price_list() -> None:
    prices = pricing.parse_litellm(pricing.fallback_document())
    assert len(prices) > 500
    index = pricing.PriceIndex({k: v.provider for k, v in prices.items()})
    assert index.match("openai", "gpt-5")[0] is not None
    assert index.match("anthropic", "claude-sonnet-4-5")[0] is not None
