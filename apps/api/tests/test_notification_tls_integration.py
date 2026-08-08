"""The pinned webhook transport, over real TLS.

The MockTransport suites prove what Drake composes. This one proves what
survives a genuine TLS handshake: the connection goes to the pinned IP,
the certificate is verified against the ORIGINAL hostname rather than that
IP, and the payload, `Host`, idempotency key and HMAC all arrive intact.

Everything is generated at runtime — a throwaway CA and two leaf
certificates, never committed and never reused.
"""

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import ssl
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from drake_api.notifications.webhook import send_webhook
from drake_api.settings import WebhookDestination
from harness_s1 import require_it_settings

pytestmark = pytest.mark.integration

PINNED_HOSTNAME = "pinned.test"


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def make_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = _key()
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name("Drake Test CA"))
        .issuer_name(_name("Drake Test CA"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        # OpenSSL 3 refuses a chain without key identifiers and without a
        # CA key usage. Getting these wrong would make the negative tests
        # below pass for the wrong reason — every certificate would be
        # rejected, including the correct one.
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def make_leaf(
    ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate, san: str
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = _key()
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name(san))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        # The SAN is the whole point: verification is against this name,
        # not against the address the socket connected to.
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, certificate


def write_pair(tmp_path: Any, name: str, key: rsa.RSAPrivateKey, cert: x509.Certificate):
    cert_file = tmp_path / f"{name}.crt"
    key_file = tmp_path / f"{name}.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


class TlsReceiver:
    """A real HTTPS endpoint on 127.0.0.1."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._server: asyncio.Server | None = None
        self.port = 0

    async def start(self, cert_file: Any, key_file: Any) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_file), str(key_file))
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, ssl=context
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            return
        lines = head.decode().split("\r\n")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ": " in line:
                name, _, value = line.partition(": ")
                headers[name.lower()] = value
        length = int(headers.get("content-length", "0"))
        body = await reader.readexactly(length) if length else b""
        self.requests.append({"request_line": lines[0], "headers": headers, "body": body})
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()


@asynccontextmanager
async def tls_receiver(cert_file: Any, key_file: Any) -> AsyncIterator[TlsReceiver]:
    receiver = TlsReceiver()
    await receiver.start(cert_file, key_file)
    try:
        yield receiver
    finally:
        await receiver.stop()


@pytest.fixture
def ca(tmp_path: Any) -> Iterator[dict[str, Any]]:
    ca_key, ca_cert = make_ca()
    ca_file = tmp_path / "ca.crt"
    ca_file.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    yield {"key": ca_key, "cert": ca_cert, "file": ca_file}


def trust(ca_file: Any) -> ssl.SSLContext:
    """A context that trusts the test CA and nothing else about it.

    `create_default_context` keeps `check_hostname` on and
    `verify_mode=CERT_REQUIRED`; this only ADDS a trust anchor.
    """
    context = ssl.create_default_context(cafile=str(ca_file))
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED
    return context


async def to_loopback(hostname: str, port: int) -> list[str]:
    """Resolve the pinned hostname to loopback, as a hostile DNS answer
    would — the point being that the socket goes exactly there and the
    certificate is still checked against the NAME."""
    return ["127.0.0.1"]


async def test_a_correct_certificate_delivers_over_real_tls(
    ca: dict[str, Any], tmp_path: Any
) -> None:
    """The whole pinned path across a genuine TLS handshake."""
    leaf_key, leaf_cert = make_leaf(ca["key"], ca["cert"], PINNED_HOSTNAME)
    cert_file, key_file = write_pair(tmp_path, "leaf", leaf_key, leaf_cert)

    secret = uuid.uuid4().hex.encode()
    secret_file = tmp_path / "signing.key"
    secret_file.write_bytes(secret)

    async with tls_receiver(cert_file, key_file) as receiver:
        destination = WebhookDestination(
            url=f"https://{PINNED_HOSTNAME}:{receiver.port}/hooks/drake",
            signing_secret_file=str(secret_file),
        )
        result = await send_webhook(
            destination,
            require_it_settings(),
            payload={"schema_version": 1, "event_type": "opened"},
            idempotency_key="idem-tls",
            resolver=to_loopback,
            ssl_context=trust(ca["file"]),
        )

    assert result.outcome == "delivered", result.error_code
    assert result.http_status == 200
    assert len(receiver.requests) == 1

    received = receiver.requests[0]
    assert received["request_line"].startswith("POST /hooks/drake")
    # The socket went to the pinned address; the receiver still sees the
    # real name, because SNI and Host both carry it.
    assert received["headers"]["host"] == f"{PINNED_HOSTNAME}:{receiver.port}"
    assert received["headers"]["idempotency-key"] == "idem-tls"

    timestamp = received["headers"]["x-drake-timestamp"]
    expected = hmac.new(
        secret, f"{timestamp}.".encode() + received["body"], hashlib.sha256
    ).hexdigest()
    assert received["headers"]["x-drake-signature"] == f"v1={expected}"
    assert json.loads(received["body"].decode())["event_type"] == "opened"


async def test_a_certificate_for_the_wrong_name_fails_verification(
    ca: dict[str, Any], tmp_path: Any
) -> None:
    """Pinning the socket must not weaken who the certificate must be for.

    The receiver here presents a valid certificate from the same CA — for
    a different name. If verification used the connected IP, or were off,
    this would succeed.
    """
    leaf_key, leaf_cert = make_leaf(ca["key"], ca["cert"], "someone-else.test")
    cert_file, key_file = write_pair(tmp_path, "wrong", leaf_key, leaf_cert)

    async with tls_receiver(cert_file, key_file) as receiver:
        destination = WebhookDestination(
            url=f"https://{PINNED_HOSTNAME}:{receiver.port}/hooks/drake"
        )
        result = await send_webhook(
            destination,
            require_it_settings(),
            payload={"schema_version": 1},
            idempotency_key="idem-wrong-san",
            resolver=to_loopback,
            ssl_context=trust(ca["file"]),
        )

    # Classified as a transport failure, and nothing was delivered.
    assert result.outcome == "retryable"
    assert result.error_code in ("connect_failed", "transport_error")
    assert receiver.requests == []


async def test_an_untrusted_issuer_fails_verification(
    ca: dict[str, Any], tmp_path: Any
) -> None:
    """A certificate for the right name from the wrong CA is still refused."""
    other_key, other_cert = make_ca()
    leaf_key, leaf_cert = make_leaf(other_key, other_cert, PINNED_HOSTNAME)
    cert_file, key_file = write_pair(tmp_path, "untrusted", leaf_key, leaf_cert)

    async with tls_receiver(cert_file, key_file) as receiver:
        destination = WebhookDestination(
            url=f"https://{PINNED_HOSTNAME}:{receiver.port}/hooks/drake"
        )
        result = await send_webhook(
            destination,
            require_it_settings(),
            payload={"schema_version": 1},
            idempotency_key="idem-untrusted",
            resolver=to_loopback,
            ssl_context=trust(ca["file"]),
        )

    assert result.outcome == "retryable"
    assert receiver.requests == []


async def test_the_tls_seam_cannot_be_used_to_turn_verification_off(
    ca: dict[str, Any],
) -> None:
    """The context parameter can only ADD trust, never remove it."""
    insecure = ssl.create_default_context(cafile=str(ca["file"]))
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE

    with pytest.raises(ValueError, match="verify certificates and hostnames"):
        await send_webhook(
            WebhookDestination(url=f"https://{PINNED_HOSTNAME}/hook"),
            require_it_settings(),
            payload={"schema_version": 1},
            idempotency_key="k",
            resolver=to_loopback,
            ssl_context=insecure,
        )
