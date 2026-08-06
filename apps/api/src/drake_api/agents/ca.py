"""Drake Agent CA operations.

The CA private key exists ONLY behind file/external-secret references
(never in the repository, database, logs, or responses). The server signs
agent CSR public keys into short-lived client certificates whose SPIFFE
URI SAN cryptographically binds cluster and agent identity.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID

from drake_api.settings import Settings

SPIFFE_TRUST_DOMAIN = "drake"


class CsrError(ValueError):
    """The CSR is unusable (never echoed to callers verbatim)."""


@dataclass(frozen=True)
class IssuedCertificate:
    certificate_pem: str
    ca_chain_pem: str
    serial: str
    not_after: dt.datetime
    public_key_pem: str


def spiffe_uri(cluster_id: uuid.UUID, agent_id: uuid.UUID) -> str:
    return f"spiffe://{SPIFFE_TRUST_DOMAIN}/cluster/{cluster_id}/agent/{agent_id}"


def load_csr(csr_pem: str) -> x509.CertificateSigningRequest:
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode())
    except Exception as error:
        raise CsrError("unparseable csr") from error
    if not csr.is_signature_valid:
        raise CsrError("csr signature invalid")
    public_key = csr.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise CsrError("unsupported key type")
    if public_key.curve.name != "secp256r1":
        raise CsrError("unsupported curve")
    return csr


class AgentCertificateAuthority:
    def __init__(self, settings: Settings) -> None:
        self._ca_cert = x509.load_pem_x509_certificate(
            Path(settings.agent_ca_cert_file).read_bytes()
        )
        ca_key = serialization.load_pem_private_key(
            Path(settings.agent_ca_key_file).read_bytes(), password=None
        )
        if not isinstance(ca_key, ec.EllipticCurvePrivateKey):
            raise RuntimeError("agent CA key must be an EC private key")
        self._ca_key = ca_key
        self._cert_ttl = dt.timedelta(days=settings.agent_cert_ttl_days)

    @property
    def ca_pem(self) -> str:
        return self._ca_cert.public_bytes(serialization.Encoding.PEM).decode()

    def sign(
        self, csr: x509.CertificateSigningRequest, cluster_id: uuid.UUID, agent_id: uuid.UUID
    ) -> IssuedCertificate:
        now = dt.datetime.now(dt.UTC)
        not_after = now + self._cert_ttl
        public_key = csr.public_key()
        certificate = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, f"agent-{agent_id}")])
            )
            .issuer_name(self._ca_cert.subject)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(not_after)
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(spiffe_uri(cluster_id, agent_id))]
                ),
                critical=False,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(self._ca_key, hashes.SHA256())
        )
        return IssuedCertificate(
            certificate_pem=certificate.public_bytes(serialization.Encoding.PEM).decode(),
            ca_chain_pem=self.ca_pem,
            serial=format(certificate.serial_number, "x"),
            not_after=not_after,
            public_key_pem=public_key.public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode(),
        )


def generate_ephemeral_ca(directory: Path) -> tuple[Path, Path]:
    """Local/test helper: mint a throwaway CA on disk (never committed)."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "drake-agent-ca")]))
        .issuer_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "drake-agent-ca")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / "agent-ca.pem"
    key_path = directory / "agent-ca-key.pem"
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
