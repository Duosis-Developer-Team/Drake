"""Test-side agent identity helpers (mirror of the Go agent's crypto)."""

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def generate_keypair() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def make_csr(key: ec.EllipticCurvePrivateKey, common_name: str = "drake-agent") -> str:
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def pop_headers(
    key: ec.EllipticCurvePrivateKey,
    agent_id: str,
    method: str,
    path: str,
    body: bytes,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    stamp = str(timestamp if timestamp is not None else int(datetime.now(UTC).timestamp()))
    nonce_value = nonce or str(uuid.uuid4())
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{method}\n{path}\n{body_hash}\n{stamp}\n{nonce_value}".encode()
    signature = key.sign(message, ec.ECDSA(hashes.SHA256()))
    return {
        "X-Drake-Agent-Id": agent_id,
        "X-Drake-Agent-Timestamp": stamp,
        "X-Drake-Agent-Nonce": nonce_value,
        "X-Drake-Agent-Signature": base64.b64encode(signature).decode(),
    }


def make_server_tls(directory: Path, hostname: str = "127.0.0.1") -> tuple[Path, Path]:
    """Ephemeral self-signed server certificate for the internal listener."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, hostname)]))
        .issuer_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, hostname)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(__import__("ipaddress").ip_address(hostname))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / "server.pem"
    key_path = directory / "server-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return cert_path, key_path


def write_client_identity(
    directory: Path, certificate_pem: str, key: ec.EllipticCurvePrivateKey
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / "agent.pem"
    key_path = directory / "agent-key.pem"
    cert_path.write_text(certificate_pem)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return cert_path, key_path
