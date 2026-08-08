"""Local email/password sign-in: the security contract, end to end.

These run against the real database and Redis. The credentials here are
test-only and deliberately unrelated to any deployment's.
"""

import asyncio
import uuid

import httpx
import pytest
from drake_api.auth.local import hash_password, normalize_email, verify_password
from drake_api.main import create_app
from harness_s1 import require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

# Test-only. No deployment uses these.
TEST_EMAIL = "local-auth-test@drake.test"
TEST_PASSWORD = "test-only-password-9f3a"


def local_app(**overrides):
    settings = require_it_settings().model_copy(update={"auth_mode": "local", **overrides})
    return create_app(settings), settings


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    )


async def _seed_credential(engine: AsyncEngine, email: str, password: str) -> str:
    normalized = normalize_email(email)
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM local_credentials WHERE email_normalized = :e"), {"e": normalized}
        )
        await connection.execute(
            text("DELETE FROM identities WHERE issuer = 'local' AND subject = :s"),
            {"s": normalized},
        )
        row = (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, display_name, email) "
                    "VALUES ('local', :s, 'Test User', :s) RETURNING id"
                ),
                {"s": normalized},
            )
        ).first()
        assert row is not None
        identity_id = row[0]
        await connection.execute(
            text(
                "INSERT INTO local_credentials (identity_id, email_normalized, password_hash) "
                "VALUES (:i, :e, :h)"
            ),
            {"i": identity_id, "e": normalized, "h": hash_password(password)},
        )
    return str(identity_id)


@pytest.mark.asyncio
async def test_correct_credentials_sign_in_and_reach_me(engine: AsyncEngine) -> None:
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    app, _ = local_app()
    async with client(app) as http:
        response = await http.post(
            "/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "signed_in"
        assert response.json()["csrf_token"]

        me = await http.get("/v1/me")
        assert me.status_code == 200
        assert me.json()["identity"]["email"] == normalize_email(TEST_EMAIL)
        assert me.json()["identity"]["issuer"] == "local"


@pytest.mark.asyncio
async def test_the_session_survives_a_reload(engine: AsyncEngine) -> None:
    """A second request on the same cookie is still signed in."""
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    app, _ = local_app()
    async with client(app) as http:
        await http.post("/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
        for _ in range(3):
            assert (await http.get("/v1/me")).status_code == 200


@pytest.mark.asyncio
async def test_a_wrong_password_and_an_unknown_account_are_indistinguishable(
    engine: AsyncEngine,
) -> None:
    """Anything else would make this endpoint an account-enumeration oracle."""
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    app, _ = local_app()
    async with client(app) as http:
        wrong = await http.post(
            "/v1/auth/login", json={"email": TEST_EMAIL, "password": "not-the-password"}
        )
        unknown = await http.post(
            "/v1/auth/login",
            json={"email": f"absent-{uuid.uuid4()}@drake.test", "password": "not-the-password"},
        )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert "password" not in wrong.text.lower() or "invalid email or password" in wrong.text


@pytest.mark.asyncio
async def test_the_password_is_never_stored_or_returned(engine: AsyncEngine) -> None:
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    app, _ = local_app()
    async with client(app) as http:
        response = await http.post(
            "/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        assert TEST_PASSWORD not in response.text
        me = await http.get("/v1/me")
        assert TEST_PASSWORD not in me.text

    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text("SELECT password_hash FROM local_credentials WHERE email_normalized = :e"),
                {"e": normalize_email(TEST_EMAIL)},
            )
        ).scalar_one()
    assert TEST_PASSWORD not in stored
    assert stored.startswith("$argon2id$")
    assert verify_password(stored, TEST_PASSWORD)

    # ...and the audit trail records the attempt without the secret.
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT metadata::text FROM audit_events WHERE action = 'auth.login' "
                    "ORDER BY occurred_at DESC LIMIT 5"
                )
            )
        ).all()
    for (metadata,) in rows:
        assert TEST_PASSWORD not in metadata
        assert "argon2" not in metadata


@pytest.mark.asyncio
async def test_email_is_normalized_before_lookup(engine: AsyncEngine) -> None:
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    app, _ = local_app()
    async with client(app) as http:
        response = await http.post(
            "/v1/auth/login",
            json={"email": f"  {TEST_EMAIL.upper()}  ", "password": TEST_PASSWORD},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_repeated_failures_are_throttled(engine: AsyncEngine) -> None:
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    app, _ = local_app(local_login_max_attempts=3, local_login_window_seconds=60)
    async with client(app) as http:
        statuses = [
            (
                await http.post("/v1/auth/login", json={"email": TEST_EMAIL, "password": "wrong"})
            ).status_code
            for _ in range(5)
        ]
    assert 429 in statuses, f"expected throttling, saw {statuses}"


@pytest.mark.asyncio
async def test_login_fails_closed_when_redis_is_unreachable(engine: AsyncEngine) -> None:
    """No session may be issued when the store that holds it is down."""
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    # A port nothing listens on: the client fails rather than hangs.
    app, _ = local_app(redis_url="redis://127.0.0.1:59999/0")
    async with client(app) as http:
        response = await asyncio.wait_for(
            http.post("/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}),
            timeout=30,
        )
    assert response.status_code == 503
    assert "signed_in" not in response.text


@pytest.mark.asyncio
async def test_each_login_issues_a_fresh_session(engine: AsyncEngine) -> None:
    """Session fixation: the caller never keeps an identifier it had before."""
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    app, settings = local_app()
    name = settings.effective_session_cookie_name
    async with client(app) as http:
        first = await http.post(
            "/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        first_cookie = first.cookies.get(name)
        second = await http.post(
            "/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        second_cookie = second.cookies.get(name)
    assert first_cookie and second_cookie
    assert first_cookie != second_cookie


@pytest.mark.asyncio
async def test_logout_revokes_the_session_server_side(engine: AsyncEngine) -> None:
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    app, _ = local_app()
    async with client(app) as http:
        login = await http.post(
            "/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        csrf = login.json()["csrf_token"]
        assert (await http.get("/v1/me")).status_code == 200

        out = await http.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert out.status_code == 200
        assert (await http.get("/v1/me")).status_code == 401


@pytest.mark.asyncio
async def test_logout_requires_the_csrf_token(engine: AsyncEngine) -> None:
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    app, _ = local_app()
    async with client(app) as http:
        await http.post("/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
        assert (await http.post("/v1/auth/logout")).status_code == 403
        assert (await http.get("/v1/me")).status_code == 200, "the session must survive"


@pytest.mark.asyncio
async def test_a_disabled_identity_cannot_sign_in(engine: AsyncEngine) -> None:
    identity_id = await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE identities SET status = 'disabled' WHERE id = :i"), {"i": identity_id}
        )
    app, _ = local_app()
    async with client(app) as http:
        response = await http.post(
            "/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_the_endpoint_does_not_exist_in_oidc_mode(engine: AsyncEngine) -> None:
    """Two ways in must never be open at once."""
    await _seed_credential(engine, TEST_EMAIL, TEST_PASSWORD)
    settings = require_it_settings().model_copy(update={"auth_mode": "oidc"})
    async with client(create_app(settings)) as http:
        response = await http.post(
            "/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_protected_routes_stay_401_before_signing_in(engine: AsyncEngine) -> None:
    app, _ = local_app()
    async with client(app) as http:
        for path in ("/v1/me", "/v1/projects", "/v1/audit-events"):
            assert (await http.get(path)).status_code == 401, path
