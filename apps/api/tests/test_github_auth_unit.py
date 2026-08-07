"""GitHub App authentication unit tests.

No PEM is ever committed: every RSA key here is generated at runtime and
lives only for the duration of the test.
"""

import datetime as dt

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from drake_api.github_app.auth import (
    IAT_BACKDATE_SECONDS,
    MAX_JWT_TTL_SECONDS,
    GitHubAppAuth,
    GitHubAuthError,
    InstallationToken,
    InstallationTokenCache,
    TokenScope,
    load_webhook_secret,
    looks_like_installation_token,
)
from drake_api.testing import make_settings


def _rsa_pem(tmp_path, name: str = "app-key.pem") -> tuple[str, str]:
    """Generate a throwaway RSA keypair; return (private path, public PEM)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path = tmp_path / name
    path.write_bytes(private_pem)
    path.chmod(0o600)
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return str(path), public_pem


def _settings(tmp_path, **overrides):
    private_path, public_pem = _rsa_pem(tmp_path)
    base = make_settings(
        github_app_enabled=True,
        github_app_client_id="Iv1.testclientid",
        github_app_private_key_file=private_path,
        **overrides,
    )
    return base, public_pem


def test_app_jwt_is_rs256_with_documented_claims(tmp_path) -> None:
    settings, public_pem = _settings(tmp_path)
    # Deliberately in the past so the claims decode is not itself immature.
    frozen = dt.datetime.now(dt.UTC) - dt.timedelta(hours=6)
    auth = GitHubAppAuth(settings, clock=lambda: frozen)

    minted = auth.mint_app_jwt()
    header = jwt.get_unverified_header(minted.token)
    assert header["alg"] == "RS256", "GitHub requires RS256"

    claims = jwt.decode(
        minted.token, public_pem, algorithms=["RS256"], options={"verify_exp": False}
    )
    # iat is backdated to absorb clock drift, exactly as GitHub recommends.
    assert claims["iat"] == int(frozen.timestamp()) - IAT_BACKDATE_SECONDS
    assert claims["exp"] == int(frozen.timestamp()) + settings.github_jwt_ttl_seconds
    assert claims["iss"] == "Iv1.testclientid"
    # The lifetime stays inside GitHub's hard ceiling.
    assert claims["exp"] - claims["iat"] <= MAX_JWT_TTL_SECONDS + IAT_BACKDATE_SECONDS


def test_app_id_is_accepted_when_no_client_id(tmp_path) -> None:
    settings, public_pem = _settings(tmp_path)
    settings = settings.model_copy(update={"github_app_client_id": "", "github_app_id": "123456"})
    auth = GitHubAppAuth(settings)
    claims = jwt.decode(
        auth.mint_app_jwt().token, public_pem, algorithms=["RS256"], options={"verify_exp": False}
    )
    assert claims["iss"] == "123456"


def test_jwt_lifetime_above_the_ceiling_is_refused(tmp_path) -> None:
    settings, _ = _settings(tmp_path)
    auth = GitHubAppAuth(settings)
    with pytest.raises(GitHubAuthError):
        auth.mint_app_jwt(ttl_seconds=MAX_JWT_TTL_SECONDS + 1)
    with pytest.raises(GitHubAuthError):
        auth.mint_app_jwt(ttl_seconds=0)


def test_expired_and_not_yet_valid_tokens_are_rejected_by_a_verifier(tmp_path) -> None:
    """Prove the claims we emit are the ones a verifier enforces."""
    settings, public_pem = _settings(tmp_path)
    past = dt.datetime.now(dt.UTC) - dt.timedelta(hours=6)
    expired = GitHubAppAuth(settings, clock=lambda: past).mint_app_jwt(ttl_seconds=60)
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(expired.token, public_pem, algorithms=["RS256"])

    future = dt.datetime.now(dt.UTC) + dt.timedelta(hours=2)
    not_yet = GitHubAppAuth(settings, clock=lambda: future).mint_app_jwt(ttl_seconds=60)
    with pytest.raises(jwt.ImmatureSignatureError):
        jwt.decode(not_yet.token, public_pem, algorithms=["RS256"], leeway=0)


def test_wrong_algorithm_is_refused_by_the_verifier(tmp_path) -> None:
    settings, public_pem = _settings(tmp_path)
    minted = GitHubAppAuth(settings).mint_app_jwt()
    with pytest.raises(jwt.InvalidAlgorithmError):
        jwt.decode(minted.token, public_pem, algorithms=["HS256"])


def test_malformed_pem_fails_closed(tmp_path) -> None:
    broken = tmp_path / "broken.pem"
    broken.write_text("not a key at all")
    settings = make_settings(
        github_app_enabled=True,
        github_app_client_id="Iv1.x",
        github_app_private_key_file=str(broken),
    )
    with pytest.raises(GitHubAuthError):
        GitHubAppAuth(settings).mint_app_jwt()


def test_truncated_pem_body_fails_closed(tmp_path) -> None:
    private_path, _ = _rsa_pem(tmp_path)
    body = open(private_path).read()
    truncated = tmp_path / "truncated.pem"
    truncated.write_text(body[: len(body) // 2])
    settings = make_settings(
        github_app_enabled=True,
        github_app_client_id="Iv1.x",
        github_app_private_key_file=str(truncated),
    )
    with pytest.raises(GitHubAuthError):
        GitHubAppAuth(settings).mint_app_jwt()


def test_missing_key_reference_fails_closed() -> None:
    settings = make_settings(github_app_enabled=True, github_app_client_id="Iv1.x")
    with pytest.raises(GitHubAuthError):
        GitHubAppAuth(settings).mint_app_jwt()


def test_issuer_missing_fails_closed(tmp_path) -> None:
    private_path, _ = _rsa_pem(tmp_path)
    settings = make_settings(github_app_private_key_file=private_path)
    with pytest.raises(GitHubAuthError):
        GitHubAppAuth(settings).mint_app_jwt()


def test_jwt_and_key_never_appear_in_repr_or_errors(tmp_path) -> None:
    settings, _ = _settings(tmp_path)
    minted = GitHubAppAuth(settings).mint_app_jwt()
    assert minted.token not in repr(minted)
    assert minted.token not in str(minted)
    assert "<redacted>" in repr(minted)

    token = InstallationToken(
        token="ghs_notarealtokenvalue", expires_at=dt.datetime.now(dt.UTC), permissions={}
    )
    assert "ghs_notarealtokenvalue" not in repr(token)
    assert "ghs_notarealtokenvalue" not in str(token)


# --- installation token cache ------------------------------------------


def _token(expires_in: int, value: str = "ghs_example") -> InstallationToken:
    return InstallationToken(
        token=value,
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in),
        permissions={"metadata": "read"},
    )


def test_cache_returns_a_live_token_and_drops_one_inside_the_buffer() -> None:
    cache = InstallationTokenCache(refresh_buffer_seconds=300)
    live = TokenScope.build(42, [1], {"metadata": "read"})
    cache.put(live, _token(3600))
    assert cache.get(live) is not None

    # A token that expires INSIDE the buffer is already unusable.
    expiring = TokenScope.build(43, [1], {"metadata": "read"})
    cache.put(expiring, _token(120))
    assert cache.get(expiring) is None, "a token inside the refresh buffer must be refreshed"


def test_cache_is_installation_scoped_and_invalidatable() -> None:
    cache = InstallationTokenCache(refresh_buffer_seconds=0)
    one = TokenScope.build(1, [11], {"metadata": "read"})
    two = TokenScope.build(2, [22], {"metadata": "read"})
    cache.put(one, _token(3600, "ghs_one"))
    cache.put(two, _token(3600, "ghs_two"))
    assert cache.get(one) is not None and cache.get(two) is not None
    assert cache.get(one).token != cache.get(two).token
    cache.invalidate(1)
    assert cache.get(one) is None
    assert cache.get(two) is not None
    cache.clear()
    assert cache.get(two) is None


def test_expired_token_is_never_served() -> None:
    cache = InstallationTokenCache(refresh_buffer_seconds=0)
    scope = TokenScope.build(7, [77], {"metadata": "read"})
    cache.put(scope, _token(-1))
    assert cache.get(scope) is None


@pytest.mark.parametrize(
    "value",
    [
        "ghs_" + "a" * 36,
        "ghs_" + "b" * 82,  # longer format — length must NOT be assumed
        "ghs_" + "1234567890",
    ],
)
def test_variable_length_ghs_tokens_are_accepted(value: str) -> None:
    assert looks_like_installation_token(value)
    cache = InstallationTokenCache(refresh_buffer_seconds=0)
    cache.put(9, _token(3600, value))
    assert cache.get(9).token == value


@pytest.mark.parametrize("value", ["", "ghs_", "ghp_abcdef", "token", "gho_abc"])
def test_non_installation_token_shapes_are_rejected(value: str) -> None:
    assert not looks_like_installation_token(value)


def test_webhook_secret_reads_from_reference_and_fails_closed(tmp_path) -> None:
    secret_path = tmp_path / "webhook-secret"
    secret_path.write_text("  local-only-test-secret\n")
    settings = make_settings(github_webhook_secret_file=str(secret_path))
    assert load_webhook_secret(settings) == "local-only-test-secret"

    with pytest.raises(GitHubAuthError):
        load_webhook_secret(make_settings())
    with pytest.raises(GitHubAuthError):
        load_webhook_secret(make_settings(github_webhook_secret_file=str(tmp_path / "absent")))
