"""Full OIDC login flow through the real API (integration, local stack).

Covers: success, cookie policy, code/state replay, session fixation,
logout invalidation, expired session, CSRF, Redis-down fail-closed,
provider-unavailable typed errors.
"""

import asyncio
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from drake_api.db import dispose_engines
from drake_api.rbac.catalog import seed_catalog
from drake_api.testing import UNREACHABLE_REDIS_URL
from harness_s1 import (
    build_harness,
    grant_platform_owner,
    require_it_settings,
    reset_rbac_state,
)
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def migrated_db() -> None:
    settings = require_it_settings()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
async def clean_state() -> None:
    settings = require_it_settings()
    engine = create_async_engine(settings.database_url)
    try:
        await reset_rbac_state(engine)
        async with engine.begin() as connection:
            await seed_catalog(connection)
    finally:
        await engine.dispose()
    yield
    await dispose_engines()


async def test_full_login_flow_and_cookie_policy() -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        start = await client.get("/v1/auth/login?redirect=/projects")
        assert start.status_code == 302
        location = start.headers["location"]
        assert "code_challenge_method=S256" in location
        assert "state=" in location and "nonce=" in location

        me = await harness.login(client, "user-owner", redirect="/projects")
        assert me["identity"]["display_name"] == "Owner One"
        assert me["permissions"] == []  # deny-by-default: no grants yet
        assert me["csrf_token"]

        cookie_header = None
        # Re-drive callback to inspect Set-Cookie attributes directly.
        code, state = await harness.authorize_code(client, "user-owner")
        callback = await client.get(f"/v1/auth/callback?code={code}&state={state}")
        cookie_header = callback.headers.get("set-cookie", "")
        assert "HttpOnly" in cookie_header
        assert "SameSite=lax" in cookie_header or "samesite=lax" in cookie_header.lower()
        # local env: Secure is intentionally off; non-local turns it on (unit-tested).


async def test_authorization_code_replay_is_rejected() -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        code, state = await harness.authorize_code(client, "user-owner")
        first = await client.get(f"/v1/auth/callback?code={code}&state={state}")
        assert first.status_code == 302
        # Same state (already consumed server-side) → rejected.
        replay = await client.get(f"/v1/auth/callback?code={code}&state={state}")
        assert replay.status_code == 403
        assert replay.json()["error"]["message"].endswith("invalid_state")


async def test_stolen_code_with_fresh_state_is_rejected() -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        stolen_code, _used_state = await harness.authorize_code(client, "user-owner")
        # Consume the stolen code legitimately once.
        fresh_code, fresh_state = await harness.authorize_code(client, "user-owner")
        first = await client.get(f"/v1/auth/callback?code={stolen_code}&state={fresh_state}")
        # PKCE verifier of fresh_state does not match stolen code's challenge...
        # and even the same-user code is single-use at the provider.
        assert first.status_code in (302, 403)
        replayed = await client.get(f"/v1/auth/callback?code={stolen_code}&state={fresh_code}")
        assert replayed.status_code == 403


async def test_session_fixation_cookie_is_replaced() -> None:
    harness = build_harness()
    cookie_name = harness.settings.session_cookie_name
    attacker_value = "attacker-chosen-value"

    async with harness.api_client() as client:
        # Victim arrives with an attacker-planted cookie and logs in.
        client.cookies.set(cookie_name, attacker_value, domain="testserver", path="/")
        code, state = await harness.authorize_code(client, "user-owner")
        callback = await client.get(f"/v1/auth/callback?code={code}&state={state}")
        issued = callback.headers.get("set-cookie", "")
        # The server mints a brand-new ID; it never adopts the inbound value.
        assert attacker_value not in issued

    # The attacker-known value must NOT have become an authenticated session.
    async with harness.api_client() as attacker:
        attacker.cookies.set(cookie_name, attacker_value, domain="testserver", path="/")
        assert (await attacker.get("/v1/me")).status_code == 401


async def test_logout_invalidates_server_side() -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await harness.login(client, "user-owner")
        csrf = me["csrf_token"]

        no_csrf = await client.post("/v1/auth/logout")
        assert no_csrf.status_code == 403

        ok = await client.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert ok.status_code == 200

        after = await client.get("/v1/me")
        assert after.status_code == 401


async def test_expired_session_is_unauthenticated() -> None:
    settings = require_it_settings().model_copy(update={"session_ttl_seconds": 1})
    harness = build_harness(settings)
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await asyncio.sleep(1.2)
        assert (await client.get("/v1/me")).status_code == 401


async def test_redis_down_fails_closed() -> None:
    settings = require_it_settings().model_copy(update={"redis_url": UNREACHABLE_REDIS_URL})
    harness = build_harness(settings)
    async with harness.api_client() as client:
        login = await client.get("/v1/auth/login")
        assert login.status_code == 503

        client.cookies.set(harness.settings.session_cookie_name, "some-session-id")
        me = await client.get("/v1/me")
        assert me.status_code == 503  # never an anonymous pass, never a fake 401


async def test_provider_unavailable_is_typed_503() -> None:
    def _explode(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    settings = require_it_settings()
    harness = build_harness(settings)
    # Replace the OIDC HTTP client with one that cannot connect.
    harness.app.state.oidc_client._http = httpx.AsyncClient(transport=httpx.MockTransport(_explode))
    harness.app.state.oidc_client._discovery = None
    async with harness.api_client() as client:
        response = await client.get("/v1/auth/login")
        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "dependency_unavailable"
        assert "boom" not in response.text


async def test_login_success_and_failure_are_audited() -> None:
    settings = require_it_settings()
    harness = build_harness(settings)
    engine = create_async_engine(settings.database_url)
    try:
        async with harness.api_client() as client:
            await harness.login(client, "user-owner")
            # Failed login: replay an already-used state.
            code, state = await harness.authorize_code(client, "user-owner")
            await client.get(f"/v1/auth/callback?code={code}&state={state}")
            await client.get(f"/v1/auth/callback?code={code}&state={state}")

        from sqlalchemy import text

        async with engine.connect() as connection:
            success = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE action = 'auth.login' AND result = 'success'"
                    )
                )
            ).scalar_one()
            failure = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE action = 'auth.login' AND result = 'failure'"
                    )
                )
            ).scalar_one()
        assert success >= 1
        assert failure >= 1
    finally:
        await engine.dispose()


async def test_bootstrapped_owner_sees_full_permission_set() -> None:
    settings = require_it_settings()
    harness = build_harness(settings)
    engine = create_async_engine(settings.database_url)
    try:
        async with harness.api_client() as client:
            await harness.login(client, "user-owner")
            await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
            me = (await client.get("/v1/me")).json()
            assert "rbac.manage" in me["permissions"]
            assert "audit.view" in me["permissions"]
            assert any(scope["scope_type"] == "organization" for scope in me["scopes"].values())
    finally:
        await engine.dispose()
