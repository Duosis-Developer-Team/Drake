"""E2E-only TLS material for the internal agent listener (local/test).

Generates into .e2e-agent/ (gitignored, disposable):
- an ephemeral Drake Agent CA (signs agent client certificates),
- a self-signed server certificate for the 127.0.0.1 internal listener.

Nothing here is production material; everything is regenerated per run.
"""

import ipaddress
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from drake_api.agents.ca import generate_ephemeral_ca


def make_server_tls(directory: Path, hostname: str = "127.0.0.1") -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, hostname)]))
        .issuer_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, hostname)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(hostname))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "internal-server.pem"
    key_path = directory / "internal-server-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return cert_path, key_path


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".e2e-agent")
    target.mkdir(parents=True, exist_ok=True)
    ca_cert, ca_key = generate_ephemeral_ca(target / "ca")
    server_cert, server_key = make_server_tls(target)
    config = {
        "agent_ca_cert_file": str(ca_cert),
        "agent_ca_key_file": str(ca_key),
        "internal_server_cert": str(server_cert),
        "internal_server_key": str(server_key),
    }
    (target / "tls.json").write_text(json.dumps(config, indent=2))
    print(f"[e2e-agent-tls] material written to {target}/")


if __name__ == "__main__":
    main()
