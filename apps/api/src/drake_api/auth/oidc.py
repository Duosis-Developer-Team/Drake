"""OIDC client: discovery, JWKS (with rotation), token exchange, validation.

Validation is strict and fail-closed: issuer, audience, signature, ``exp``,
``nbf``, and ``nonce`` are all required to be correct. Provider errors are
mapped to typed errors that never carry raw tokens or provider bodies.
"""

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

from drake_api.settings import Settings

JWKS_CACHE_TTL_SECONDS = 300


class OidcError(RuntimeError):
    """Typed OIDC failure. code is safe for clients; message stays generic."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(slots=True)
class ValidatedIdentity:
    issuer: str
    subject: str
    display_name: str
    email: str
    groups: list[str]
    groups_overage: bool


@dataclass
class _JwksCache:
    keys: dict[str, Any] = field(default_factory=dict)
    fetched_at: float = 0.0


class OidcClient:
    """Minimal, strict OIDC client.

    ``http_client`` is injectable so tests can point it at an in-process fake
    provider; production uses a real client against the configured issuer.
    """

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http_client or httpx.AsyncClient(timeout=10.0)
        self._discovery: dict[str, Any] | None = None
        self._jwks = _JwksCache()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _discover(self) -> dict[str, Any]:
        if self._discovery is None:
            url = self._settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
            try:
                response = await self._http.get(url)
                response.raise_for_status()
                self._discovery = response.json()
            except Exception as error:
                raise OidcError("provider_unavailable") from error
        return self._discovery

    async def authorization_url(self) -> str:
        discovery = await self._discover()
        endpoint: str = discovery["authorization_endpoint"]
        return endpoint

    async def _jwks_keys(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        expired = now - self._jwks.fetched_at > JWKS_CACHE_TTL_SECONDS
        if force_refresh or expired or not self._jwks.keys:
            discovery = await self._discover()
            try:
                response = await self._http.get(discovery["jwks_uri"])
                response.raise_for_status()
                payload = response.json()
            except Exception as error:
                raise OidcError("provider_unavailable") from error
            keys: dict[str, Any] = {}
            for jwk in payload.get("keys", []):
                if "kid" in jwk:
                    keys[jwk["kid"]] = jwk
            self._jwks = _JwksCache(keys=keys, fetched_at=now)
        return self._jwks.keys

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange an authorization code (with PKCE verifier) for tokens."""
        discovery = await self._discover()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._settings.oidc_redirect_url,
            "client_id": self._settings.oidc_client_id,
            "code_verifier": code_verifier,
        }
        if self._settings.oidc_client_secret:
            data["client_secret"] = self._settings.oidc_client_secret
        try:
            response = await self._http.post(discovery["token_endpoint"], data=data)
        except Exception as error:
            raise OidcError("provider_unavailable") from error
        if response.status_code != 200:
            # Covers invalid/replayed codes and PKCE mismatch. Never include
            # the provider's raw body in errors.
            raise OidcError("token_exchange_failed")
        payload: dict[str, Any] = response.json()
        if "id_token" not in payload:
            raise OidcError("token_exchange_failed")
        return payload

    async def validate_id_token(self, id_token: str, expected_nonce: str) -> ValidatedIdentity:
        """Validate signature, issuer, audience, exp, nbf, and nonce."""
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as error:
            raise OidcError("invalid_token") from error

        kid = header.get("kid")
        if not kid:
            raise OidcError("invalid_token")

        keys = await self._jwks_keys()
        if kid not in keys:
            # Key rotation: one forced refresh, then fail.
            keys = await self._jwks_keys(force_refresh=True)
        jwk = keys.get(kid)
        if jwk is None:
            raise OidcError("unknown_signing_key")

        try:
            public_key = jwt.PyJWK(jwk).key
            claims = jwt.decode(
                id_token,
                key=public_key,
                algorithms=["RS256"],
                audience=self._settings.oidc_client_id,
                issuer=self._settings.oidc_issuer,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                },
                leeway=5,
            )
        except jwt.InvalidIssuerError as error:
            raise OidcError("invalid_issuer") from error
        except jwt.InvalidAudienceError as error:
            raise OidcError("invalid_audience") from error
        except jwt.ExpiredSignatureError as error:
            raise OidcError("token_expired") from error
        except jwt.ImmatureSignatureError as error:
            raise OidcError("token_not_yet_valid") from error
        except jwt.PyJWTError as error:
            raise OidcError("invalid_signature") from error

        if claims.get("nonce") != expected_nonce:
            raise OidcError("invalid_nonce")

        groups_claim = claims.get("groups")
        groups: list[str] = list(groups_claim) if isinstance(groups_claim, list) else []
        # Entra group overage: groups omitted and signalled via _claim_names /
        # hasgroups. Fail closed: no group-derived permissions in that case.
        overage = bool(
            not groups
            and (claims.get("hasgroups") is True or "groups" in (claims.get("_claim_names") or {}))
        )

        return ValidatedIdentity(
            issuer=str(claims["iss"]),
            subject=str(claims["sub"]),
            display_name=str(claims.get("name") or claims.get("preferred_username") or ""),
            email=str(claims.get("email") or ""),
            groups=[str(group) for group in groups],
            groups_overage=overage,
        )
