"""Server-side session store: hashed keys, invalidation, fail-closed, CSRF."""

import pytest
from drake_api.auth.flows import sanitize_post_login_redirect
from drake_api.auth.sessions import (
    Session,
    SessionBackendUnavailableError,
    SessionStore,
    storage_key,
)
from drake_api.testing import make_settings
from fakeredis import aioredis as fakeaioredis


def make_store() -> SessionStore:
    return SessionStore(make_settings(), client=fakeaioredis.FakeRedis())


def sample_session() -> Session:
    return Session(
        identity_id="00000000-0000-0000-0000-000000000001",
        issuer="http://fake-oidc.test",
        subject="user-owner",
        display_name="Owner One",
        email="owner@example.test",
    )


async def test_create_and_get_roundtrip() -> None:
    store = make_store()
    session_id = await store.create(sample_session())
    loaded = await store.get(session_id)
    assert loaded is not None
    assert loaded.subject == "user-owner"
    assert loaded.csrf_token  # generated automatically


async def test_storage_key_is_hashed_not_raw() -> None:
    session_id = "raw-session-id-material"
    key = storage_key(session_id)
    assert session_id not in key
    assert len(key.split(":")[-1]) == 64  # sha256 hex


async def test_each_login_gets_a_fresh_session_id() -> None:
    store = make_store()
    first = await store.create(sample_session())
    second = await store.create(sample_session())
    assert first != second  # fixation defense: IDs are never reused


async def test_delete_invalidates_server_side() -> None:
    store = make_store()
    session_id = await store.create(sample_session())
    await store.delete(session_id)
    assert await store.get(session_id) is None


async def test_unknown_session_id_is_none() -> None:
    store = make_store()
    assert await store.get("never-issued") is None


async def test_login_state_is_single_use() -> None:
    store = make_store()
    await store.put_login_state("state-1", {"nonce": "n", "verifier": "v", "redirect": "/"})
    first = await store.take_login_state("state-1")
    second = await store.take_login_state("state-1")
    assert first is not None and first["nonce"] == "n"
    assert second is None  # replayed state fails


async def test_backend_down_is_fail_closed_typed_error() -> None:
    from redis import asyncio as aioredis

    unreachable = aioredis.Redis(
        host="127.0.0.1", port=59379, socket_connect_timeout=0.2, socket_timeout=0.2
    )
    store = SessionStore(make_settings(), client=unreachable)
    with pytest.raises(SessionBackendUnavailableError) as excinfo:
        await store.get("anything")
    assert "59379" not in str(excinfo.value)
    with pytest.raises(SessionBackendUnavailableError):
        await store.create(sample_session())


def test_post_login_redirect_allowlist() -> None:
    assert sanitize_post_login_redirect("/projects") == "/projects"
    assert sanitize_post_login_redirect("/a/b?c=1&d=2") == "/a/b?c=1&d=2"
    # Open-redirect attempts collapse to "/":
    assert sanitize_post_login_redirect("//evil.example.test") == "/"
    assert sanitize_post_login_redirect("http://evil.example.test") == "/"
    assert sanitize_post_login_redirect("https://evil.example.test/x") == "/"
    assert sanitize_post_login_redirect("/\\evil.example.test") == "/"
    assert sanitize_post_login_redirect("javascript:alert(1)") == "/"
    assert sanitize_post_login_redirect(None) == "/"
    assert sanitize_post_login_redirect("") == "/"
