"""E2E-only GitHub App material (local/test only, fail-closed elsewhere).

Generates a throwaway RSA key and webhook secret into `.e2e-github/`
(gitignored). Nothing here is a real credential, and nothing is printed
to stdout except the paths.
"""

import os
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

TARGET = Path(".e2e-github")


def main() -> None:
    env = os.environ.get("DRAKE_ENV", "local")
    if env not in ("local", "test"):
        raise RuntimeError("e2e github config is local/test only")
    TARGET.mkdir(parents=True, exist_ok=True)

    key_path = TARGET / "app-key.pem"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)

    secret_path = TARGET / "webhook-secret"
    # Deterministic for the E2E run so the spec can sign requests, but
    # freshly generated per setup and never committed.
    configured = os.environ.get("DRAKE_E2E_GITHUB_WEBHOOK_SECRET")
    secret_path.write_text(configured or secrets.token_hex(16))
    secret_path.chmod(0o600)

    sys.stdout.write(f"e2e github material written to {TARGET}/\n")


if __name__ == "__main__":
    main()
