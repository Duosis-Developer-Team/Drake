"""Forwarded headers, cookies and CSRF at the production edge.

The property under test: a request cannot talk Drake into accepting an
origin it was not configured with. Public URLs come from configuration,
and the CSRF comparison in production uses that configured origin only.
"""

import uuid as uuidlib
from typing import Any

import pytest
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import login_all

pytestmark = pytest.mark.integration

PRODUCTION_ORIGIN = "https://drake.example.test"


def _production_harness() -> Any:
    """A production-shaped API, on the disposable local stack."""
    settings = require_it_settings().model_copy(
        update={
            "env": "prod",
            "public_origin": PRODUCTION_ORIGIN,
            "trusted_proxy_count": 1,
            "allowed_web_origins": [PRODUCTION_ORIGIN],
            "oidc_redirect_url": f"{PRODUCTION_ORIGIN}/v1/auth/callback",
            # The fake provider is https-shaped so the production guard is
            # not tripped by the harness itself.
            "oidc_issuer": "https://fake-oidc.test",
        }
    )
    return build_harness(settings)


async def test_a_forged_forwarded_host_cannot_change_the_accepted_origin(
    engine: AsyncEngine,
) -> None:
    """The header is the attack; the configured origin is the defence."""
    harness = _production_harness()
    await login_all(harness, ["user-owner"])
    await grant_platform_owner(engine, harness.provider.issuer, "user-owner")

    async with harness.api_client() as client:
        me = await harness.login(client, "user-owner")
        headers = {
            "X-CSRF-Token": me["csrf_token"],
            "Idempotency-Key": str(uuidlib.uuid4()),
            # A client claiming both the origin and the proxy metadata.
            "Origin": "https://attacker.example.test",
            "X-Forwarded-Host": "attacker.example.test",
            "X-Forwarded-Proto": "https",
        }
        response = await client.post("/v1/roles", headers=headers, json={"name": "x"})
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "origin not allowed"


async def test_the_canonical_origin_is_accepted(engine: AsyncEngine) -> None:
    harness = _production_harness()
    await login_all(harness, ["user-owner"])
    await grant_platform_owner(engine, harness.provider.issuer, "user-owner")

    async with harness.api_client() as client:
        me = await harness.login(client, "user-owner")
        response = await client.post(
            "/v1/roles",
            headers={
                "X-CSRF-Token": me["csrf_token"],
                "Idempotency-Key": str(uuidlib.uuid4()),
                "Origin": PRODUCTION_ORIGIN,
            },
            json={"name": "edge-contract-role", "permissions": ["project.view"]},
        )
    # Whatever the endpoint decides about the payload, it is not an origin
    # refusal — which is the point.
    assert response.status_code != 403


async def test_the_request_base_url_is_not_folded_into_the_allow_list(
    engine: AsyncEngine,
) -> None:
    """In development the request origin is trusted; in production it is not."""
    harness = _production_harness()
    await login_all(harness, ["user-owner"])
    await grant_platform_owner(engine, harness.provider.issuer, "user-owner")

    async with harness.api_client() as client:
        me = await harness.login(client, "user-owner")
        # The harness serves on http://testserver; that origin must NOT be
        # accepted merely because the request arrived there.
        response = await client.post(
            "/v1/roles",
            headers={
                "X-CSRF-Token": me["csrf_token"],
                "Idempotency-Key": str(uuidlib.uuid4()),
                "Origin": "http://testserver",
            },
            json={"name": "x"},
        )
    assert response.status_code == 403


async def test_the_session_cookie_is_secure_in_production(engine: AsyncEngine) -> None:
    harness = _production_harness()
    await login_all(harness, ["user-owner"])

    expected_name = harness.settings.effective_session_cookie_name
    # Over HTTPS the name carries the `__Host-` prefix, which browsers only
    # honour for a Secure, host-only, path-`/` cookie.
    assert expected_name == "__Host-drake_session"

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        cookie = next(value for name, value in client.cookies.items() if name == expected_name)
    assert cookie
    # httpx does not expose attributes directly; assert through the jar.
    jar = client.cookies.jar
    entry = next(c for c in jar if c.name == expected_name)
    assert entry.secure is True
    # host-only: a Domain attribute would widen it to every sibling host.
    assert entry.domain_specified is False
    assert entry.path == "/"


async def test_health_responses_carry_no_configuration_detail(engine: AsyncEngine) -> None:
    harness = _production_harness()
    async with harness.api_client() as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")
    for response in (live, ready):
        body = response.text
        assert "postgresql" not in body
        assert "redis://" not in body
        assert "drake.example.test" not in body
        assert "secret" not in body.lower()
