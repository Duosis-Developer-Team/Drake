"""OIDC token validation against the deterministic fake provider.

Covers: success, invalid issuer/audience/signature, expired/not-yet-valid,
invalid nonce, JWKS rotation, group claims, and group overage fail-closed.
"""

import time

import httpx
import pytest
from drake_api.auth.oidc import OidcClient, OidcError
from drake_api.testing import make_settings
from fake_oidc import DEFAULT_CLIENT_ID, DEFAULT_ISSUER, FakeOidcProvider


@pytest.fixture
def provider() -> FakeOidcProvider:
    return FakeOidcProvider()


@pytest.fixture
def oidc(provider: FakeOidcProvider) -> OidcClient:
    settings = make_settings(oidc_issuer=DEFAULT_ISSUER, oidc_client_id=DEFAULT_CLIENT_ID)
    transport = httpx.ASGITransport(app=provider.build_app())
    client = httpx.AsyncClient(transport=transport, base_url=DEFAULT_ISSUER)
    return OidcClient(settings, http_client=client)


async def test_valid_token_is_accepted(provider: FakeOidcProvider, oidc: OidcClient) -> None:
    token = provider.mint_id_token("user-owner", nonce="nonce-1")
    identity = await oidc.validate_id_token(token, expected_nonce="nonce-1")
    assert identity.subject == "user-owner"
    assert identity.issuer == DEFAULT_ISSUER
    assert identity.groups_overage is False


async def test_wrong_issuer_is_rejected(provider: FakeOidcProvider, oidc: OidcClient) -> None:
    provider.token_claim_overrides = {"iss": "http://evil.example.test"}
    token = provider.mint_id_token("user-owner", nonce="n")
    with pytest.raises(OidcError) as excinfo:
        await oidc.validate_id_token(token, expected_nonce="n")
    assert excinfo.value.code == "invalid_issuer"


async def test_wrong_audience_is_rejected(provider: FakeOidcProvider, oidc: OidcClient) -> None:
    provider.token_claim_overrides = {"aud": "someone-else"}
    token = provider.mint_id_token("user-owner", nonce="n")
    with pytest.raises(OidcError) as excinfo:
        await oidc.validate_id_token(token, expected_nonce="n")
    assert excinfo.value.code == "invalid_audience"


async def test_expired_token_is_rejected(provider: FakeOidcProvider, oidc: OidcClient) -> None:
    provider.token_claim_overrides = {"exp": int(time.time()) - 120}
    token = provider.mint_id_token("user-owner", nonce="n")
    with pytest.raises(OidcError) as excinfo:
        await oidc.validate_id_token(token, expected_nonce="n")
    assert excinfo.value.code == "token_expired"


async def test_not_yet_valid_token_is_rejected(
    provider: FakeOidcProvider, oidc: OidcClient
) -> None:
    provider.token_claim_overrides = {"nbf": int(time.time()) + 300}
    token = provider.mint_id_token("user-owner", nonce="n")
    with pytest.raises(OidcError) as excinfo:
        await oidc.validate_id_token(token, expected_nonce="n")
    assert excinfo.value.code == "token_not_yet_valid"


async def test_invalid_signature_is_rejected(provider: FakeOidcProvider, oidc: OidcClient) -> None:
    provider.sign_with_unknown_key = True
    token = provider.mint_id_token("user-owner", nonce="n")
    with pytest.raises(OidcError) as excinfo:
        await oidc.validate_id_token(token, expected_nonce="n")
    assert excinfo.value.code == "invalid_signature"


async def test_invalid_nonce_is_rejected(provider: FakeOidcProvider, oidc: OidcClient) -> None:
    token = provider.mint_id_token("user-owner", nonce="expected")
    with pytest.raises(OidcError) as excinfo:
        await oidc.validate_id_token(token, expected_nonce="different")
    assert excinfo.value.code == "invalid_nonce"


async def test_jwks_rotation_is_followed(provider: FakeOidcProvider, oidc: OidcClient) -> None:
    token_old = provider.mint_id_token("user-owner", nonce="n1")
    assert (await oidc.validate_id_token(token_old, expected_nonce="n1")).subject == "user-owner"
    # Rotate to a brand-new key (old dropped): forces a JWKS refetch by kid-miss.
    provider.rotate_key(drop_old=True)
    token_new = provider.mint_id_token("user-owner", nonce="n2")
    identity = await oidc.validate_id_token(token_new, expected_nonce="n2")
    assert identity.subject == "user-owner"


async def test_group_claims_are_surfaced(provider: FakeOidcProvider, oidc: OidcClient) -> None:
    provider.users["user-owner"].groups = ["group-a", "group-b"]
    token = provider.mint_id_token("user-owner", nonce="n")
    identity = await oidc.validate_id_token(token, expected_nonce="n")
    assert identity.groups == ["group-a", "group-b"]
    assert identity.groups_overage is False


async def test_group_overage_fails_closed(provider: FakeOidcProvider, oidc: OidcClient) -> None:
    provider.emit_group_overage = True
    token = provider.mint_id_token("user-owner", nonce="n")
    identity = await oidc.validate_id_token(token, expected_nonce="n")
    assert identity.groups == []
    assert identity.groups_overage is True


async def test_error_codes_never_carry_token_material(
    provider: FakeOidcProvider, oidc: OidcClient
) -> None:
    provider.token_claim_overrides = {"iss": "http://evil.example.test"}
    token = provider.mint_id_token("user-owner", nonce="n")
    with pytest.raises(OidcError) as excinfo:
        await oidc.validate_id_token(token, expected_nonce="n")
    assert token.split(".")[2] not in str(excinfo.value)
    assert "eyJ" not in str(excinfo.value)
