"""Deterministic fake OIDC provider for tests and local E2E ONLY.

Lives under tests/ so it is never part of the shipped package; additionally,
the API refuses plaintext issuers outside local/test (see
Settings.validate_runtime_security), so this provider cannot activate in a
production-like environment.

Supports: discovery, JWKS with key rotation, Authorization Code + PKCE
(S256), single-use codes, signed ID tokens, group claims, and failure knobs
(wrong issuer/audience, expired/not-yet-valid tokens, invalid signature,
invalid nonce, group overage).
"""

import base64
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

DEFAULT_ISSUER = "http://fake-oidc.test"
DEFAULT_CLIENT_ID = "drake-test-client"


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class FakeUser:
    subject: str
    name: str
    email: str
    groups: list[str] = field(default_factory=list)


@dataclass
class _StoredCode:
    subject: str
    nonce: str
    code_challenge: str
    redirect_uri: str
    used: bool = False


class FakeOidcProvider:
    def __init__(self, issuer: str = DEFAULT_ISSUER, client_id: str = DEFAULT_CLIENT_ID) -> None:
        self.issuer = issuer
        self.client_id = client_id
        self._keys: dict[str, rsa.RSAPrivateKey] = {}
        self._active_kid = ""
        self.rotate_key()
        self.users: dict[str, FakeUser] = {
            "user-owner": FakeUser("user-owner", "Owner One", "owner@example.test"),
            "user-plain": FakeUser("user-plain", "Plain User", "plain@example.test"),
        }
        self.default_subject = "user-owner"
        self._codes: dict[str, _StoredCode] = {}
        # Failure knobs (reset() clears them):
        self.token_claim_overrides: dict[str, Any] = {}
        self.sign_with_unknown_key = False
        self.emit_group_overage = False

    # ------------------------------------------------------------------ keys
    def rotate_key(self, *, drop_old: bool = False) -> str:
        kid = f"key-{uuid.uuid4().hex[:8]}"
        if drop_old:
            self._keys.clear()
        self._keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._active_kid = kid
        return kid

    def reset_knobs(self) -> None:
        self.token_claim_overrides = {}
        self.sign_with_unknown_key = False
        self.emit_group_overage = False

    def _jwks(self) -> dict[str, Any]:
        keys = []
        for kid, private_key in self._keys.items():
            public_numbers = private_key.public_key().public_numbers()
            keys.append(
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": kid,
                    "n": _b64url_uint(public_numbers.n),
                    "e": _b64url_uint(public_numbers.e),
                }
            )
        return {"keys": keys}

    # ----------------------------------------------------------------- token
    def mint_id_token(self, subject: str, nonce: str) -> str:
        user = self.users[subject]
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.client_id,
            "sub": user.subject,
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
            "name": user.name,
            "email": user.email,
        }
        if self.emit_group_overage:
            claims["_claim_names"] = {"groups": "src1"}
            claims["_claim_sources"] = {"src1": {"endpoint": "https://graph.example.test"}}
        elif user.groups:
            claims["groups"] = user.groups
        claims.update(self.token_claim_overrides)

        if self.sign_with_unknown_key:
            rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            key_pem = rogue.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            # Signed by a key NOT in the JWKS but claiming the active kid.
            return jwt.encode(claims, key_pem, algorithm="RS256", headers={"kid": self._active_kid})

        private = self._keys[self._active_kid]
        key_pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return jwt.encode(claims, key_pem, algorithm="RS256", headers={"kid": self._active_kid})

    # ------------------------------------------------------------------- app
    def build_app(self) -> FastAPI:
        app = FastAPI(title="Fake OIDC (test only)")
        provider = self

        @app.get("/.well-known/openid-configuration")
        async def discovery() -> dict[str, Any]:
            return {
                "issuer": provider.issuer,
                "authorization_endpoint": f"{provider.issuer}/authorize",
                "token_endpoint": f"{provider.issuer}/token",
                "jwks_uri": f"{provider.issuer}/jwks",
                "response_types_supported": ["code"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"],
            }

        @app.get("/jwks")
        async def jwks() -> dict[str, Any]:
            return provider._jwks()

        @app.get("/authorize")
        async def authorize(request: Request) -> RedirectResponse:
            params = request.query_params
            subject = params.get("login_hint") or provider.default_subject
            code = secrets.token_urlsafe(24)
            provider._codes[code] = _StoredCode(
                subject=subject,
                nonce=params.get("nonce", ""),
                code_challenge=params.get("code_challenge", ""),
                redirect_uri=params.get("redirect_uri", ""),
            )
            query = urlencode({"code": code, "state": params.get("state", "")})
            return RedirectResponse(
                url=f"{params.get('redirect_uri', '')}?{query}", status_code=302
            )

        @app.post("/token")
        async def token(request: Request) -> JSONResponse:
            # Parse urlencoded body without the python-multipart dependency.
            from urllib.parse import parse_qs

            body = (await request.body()).decode()
            form = {key: values[0] for key, values in parse_qs(body).items()}
            code = str(form.get("code", ""))
            verifier = str(form.get("code_verifier", ""))
            stored = provider._codes.get(code)
            if stored is None or stored.used:
                return JSONResponse(status_code=400, content={"error": "invalid_grant"})
            stored.used = True  # single-use: replay fails
            digest = hashlib.sha256(verifier.encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if challenge != stored.code_challenge:
                return JSONResponse(status_code=400, content={"error": "invalid_grant"})
            id_token = provider.mint_id_token(stored.subject, stored.nonce)
            return JSONResponse(
                {
                    "access_token": secrets.token_urlsafe(16),
                    "token_type": "Bearer",
                    "id_token": id_token,
                }
            )

        return app


def main() -> None:
    """Run standalone for local E2E (never in production images)."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9556)
    parser.add_argument("--issuer", default="http://127.0.0.1:9556")
    args = parser.parse_args()

    provider = FakeOidcProvider(issuer=args.issuer)
    provider.users["user-owner"].groups = []
    uvicorn.run(provider.build_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
