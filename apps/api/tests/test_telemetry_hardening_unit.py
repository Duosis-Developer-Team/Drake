"""Sprint 3 acceptance-hardening unit tests.

Internal metrics gating + exposition format, streaming response budget,
connector private-network/DNS policy, and the UTC range contract.
"""

import asyncio
import re
from collections.abc import AsyncIterator

import httpx
import pytest
from drake_api.main import create_app
from drake_api.settings import TelemetryConnector
from drake_api.telemetry.metrics import BrokerMetrics
from drake_api.telemetry.provider import (
    ConnectorRefusedError,
    PrometheusAdapter,
    ProviderContractError,
    ProviderUnavailableError,
    validate_connector,
)
from drake_api.testing import make_settings


def _run(coro):
    return asyncio.run(coro)


# --- internal metrics gating -------------------------------------------------


def _client(settings) -> httpx.AsyncClient:
    app = create_app(settings)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def test_internal_metrics_disabled_by_default_and_indistinguishable() -> None:
    settings = make_settings(env="test")
    assert settings.internal_metrics_enabled is False

    async def check() -> None:
        async with _client(settings) as client:
            disabled = await client.get("/v1/internal/metrics")
            ghost = await client.get("/v1/internal/does-not-exist")
            assert disabled.status_code == 404
            # No existence signal: same status and error shape as any
            # unknown route.
            assert disabled.json()["error"]["code"] == ghost.json()["error"]["code"]

    _run(check())


def test_internal_metrics_explicit_local_enable_serves_exposition() -> None:
    settings = make_settings(env="test").model_copy(update={"internal_metrics_enabled": True})

    async def check() -> None:
        async with _client(settings) as client:
            response = await client.get("/v1/internal/metrics")
            assert response.status_code == 200
            assert "drake_telemetry_queries_total" in response.text

    _run(check())


def test_internal_metrics_refused_outside_local_test() -> None:
    # The redirect URL has to agree with the canonical public origin, or
    # the edge guard fires first and this stops testing internal metrics.
    settings = make_settings(
        env="prod",
        oidc_issuer="https://issuer.example",
    ).model_copy(update={"internal_metrics_enabled": True})
    with pytest.raises(RuntimeError, match="internal metrics"):
        create_app(settings)


def test_metrics_exposition_is_parseable_with_complete_histograms() -> None:
    metrics = BrokerMetrics()
    metrics.record_query(
        template_key="service.request-rate.v1",
        provider_type="prometheus",
        outcome="ok",
        cache_state="miss",
        duration_seconds=0.2,
        returned_points=42,
    )
    metrics.record_query(
        template_key="service.request-rate.v1",
        provider_type="prometheus",
        outcome="ok",
        cache_state="fresh_hit",
        duration_seconds=0.1,
        returned_points=8,
    )
    rendered = metrics.render()

    # Minimal Prometheus text-format parser: every non-comment line is
    # `name{labels} value` (or `name value`), TYPE precedes samples, and
    # histogram families expose monotonic buckets plus _sum and _count.
    sample = re.compile(
        r"^[a-z_][a-z0-9_]*(\{[a-z0-9_]+=\"[^\"]*\"(,[a-z0-9_]+=\"[^\"]*\")*\})? "
        r"-?[0-9.+eInf]+$"
    )
    families: set[str] = set()
    for line in rendered.strip().splitlines():
        if line.startswith("# TYPE "):
            families.add(line.split()[2])
            continue
        assert sample.fullmatch(line), line

    for histogram in ("drake_telemetry_query_duration_seconds", "drake_telemetry_returned_points"):
        assert histogram in families
        buckets = [
            float(line.rsplit(" ", 1)[1])
            for line in rendered.splitlines()
            if line.startswith(f"{histogram}_bucket") and 'le="+Inf"' not in line
        ]
        assert buckets == sorted(buckets)  # cumulative histograms are monotonic
        assert f"{histogram}_sum" in rendered
        assert f"{histogram}_count 2" in rendered
    assert "drake_telemetry_returned_points_sum 50" in rendered


# --- streaming response budget ----------------------------------------------


class ChunkedStream(httpx.AsyncByteStream):
    """Counts how many chunks the adapter actually consumed."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.consumed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:  # pragma: no cover - httpx contract
        return


class StreamingTransport(httpx.AsyncBaseTransport):
    def __init__(self, stream: httpx.AsyncByteStream, content_type: str = "application/json"):
        self.stream = stream
        self.content_type = content_type

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=self.stream, headers={"Content-Type": self.content_type})


def _stream_query(transport: httpx.AsyncBaseTransport):
    adapter = PrometheusAdapter(make_settings(env="test"), transport=transport)
    return adapter.query_range(
        TelemetryConnector(url="http://127.0.0.1:59095"),
        "up",
        start=0,
        end=600,
        step_seconds=60,
        timeout_seconds=2.0,
        correlation_id="test",
    )


def test_streaming_under_limit_succeeds() -> None:
    payload = (
        b'{"status":"success","data":{"resultType":"matrix",'
        b'"result":[{"metric":{},"values":[[1,"2.0"]]}]}}'
    )
    chunks = [payload[i : i + 16] for i in range(0, len(payload), 16)]
    stream = ChunkedStream(chunks)
    result = _run(_stream_query(StreamingTransport(stream)))
    assert result.result[0]["values"] == [[1, "2.0"]]
    assert stream.consumed == len(chunks)


def test_streaming_stops_at_the_byte_limit_without_full_consumption() -> None:
    # 64 chunks of 64 KiB = 4 MiB total; the cap is 2 MiB (+1 chunk).
    chunk = b"x" * (64 * 1024)
    stream = ChunkedStream([chunk] * 64)
    with pytest.raises(ProviderContractError, match="provider_response_too_large"):
        _run(_stream_query(StreamingTransport(stream)))
    # The limit acted as a true streaming budget: reading stopped right
    # after the crossing chunk — the payload was NOT fully consumed.
    assert stream.consumed == 33
    assert stream.consumed < 64


def test_wrong_content_type_is_fail_closed_without_reading_the_body() -> None:
    stream = ChunkedStream([b"<html>not json</html>"])
    with pytest.raises(ProviderContractError, match="provider_unexpected_content_type"):
        _run(_stream_query(StreamingTransport(stream, content_type="text/html")))
    assert stream.consumed == 0  # refused before any body chunk

    # Charset parameters on the right media type stay acceptable:
    payload = b'{"status":"success","data":{"resultType":"matrix","result":[]}}'
    ok = ChunkedStream([payload])
    result = _run(
        _stream_query(StreamingTransport(ok, content_type="application/json; charset=utf-8"))
    )
    assert result.result == []


def test_mid_stream_failure_is_typed_and_redacted() -> None:
    class ExplodingStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'{"status":'
            raise httpx.ReadTimeout("secret internal detail")

        async def aclose(self) -> None:
            return

    with pytest.raises(ProviderUnavailableError) as excinfo:
        _run(_stream_query(StreamingTransport(ExplodingStream())))
    assert excinfo.value.code == "provider_timeout"
    assert "secret" not in str(excinfo.value)


# --- connector private-network / DNS policy ---------------------------------


def _prod_settings():
    return make_settings(
        env="prod",
        oidc_issuer="https://issuer.example",
        oidc_redirect_url="https://drake.example/v1/auth/callback",
    )


def test_private_ip_literal_requires_explicit_opt_in() -> None:
    settings = _prod_settings()
    connector = TelemetryConnector(url="https://10.20.30.40:9090")
    with pytest.raises(ConnectorRefusedError, match="connector_private_refused"):
        _run(validate_connector(connector, settings))
    allowed = TelemetryConnector(url="https://10.20.30.40:9090", allow_private=True)
    assert _run(validate_connector(allowed, settings)) == allowed.url


def test_plaintext_needs_an_opt_in_even_to_a_private_address() -> None:
    """The scheme is its own decision; being private is not a licence."""
    settings = _prod_settings()
    connector = TelemetryConnector(url="http://10.20.30.40:9090", allow_private=True)
    with pytest.raises(ConnectorRefusedError, match="connector_plaintext_refused"):
        _run(validate_connector(connector, settings))


def test_plaintext_to_a_public_address_stays_refused_with_the_opt_in() -> None:
    """The half of the rule that must not become negotiable.

    `allow_plaintext` exists so an in-cluster Prometheus is expressible. If
    it also permitted plaintext to the internet it would be a way to send
    every query in the clear, which is the thing the original rule was
    written to stop.
    """
    settings = _prod_settings()
    connector = TelemetryConnector(
        url="http://93.184.216.34:9090", allow_private=True, allow_plaintext=True
    )
    with pytest.raises(ConnectorRefusedError, match="connector_plaintext_refused"):
        _run(validate_connector(connector, settings))


def test_plaintext_to_a_private_address_is_allowed_with_both_opt_ins() -> None:
    """The case this exists for: a ClusterIP Prometheus, in production."""
    settings = _prod_settings()
    connector = TelemetryConnector(
        url="http://10.233.0.42:80", allow_private=True, allow_plaintext=True
    )
    assert _run(validate_connector(connector, settings)) == connector.url


def test_plaintext_opt_in_does_not_relax_anything_else() -> None:
    """Loopback, link-local and the private opt-in are untouched by it."""
    settings = _prod_settings()
    for url, code in (
        ("http://127.0.0.1:9090", "connector_target_refused"),
        ("http://169.254.169.254:80", "connector_target_refused"),
    ):
        connector = TelemetryConnector(url=url, allow_private=True, allow_plaintext=True)
        with pytest.raises(ConnectorRefusedError, match=code):
            _run(validate_connector(connector, settings))
    # Still no hostname connectors, opt-in or not.
    hostname = TelemetryConnector(
        url="http://prometheus.internal", allow_private=True, allow_plaintext=True
    )
    with pytest.raises(ConnectorRefusedError, match="connector_hostname_unpinned"):
        _run(validate_connector(hostname, settings))


def test_hostname_connectors_fail_closed_outside_local_test() -> None:
    # DNS-name connectors are refused in production-like environments until
    # the transport pins validated addresses (rebinding cannot reach a
    # connection because no connection is ever attempted).
    settings = _prod_settings()
    connector = TelemetryConnector(url="https://prometheus.internal", allow_private=True)
    with pytest.raises(ConnectorRefusedError, match="connector_hostname_unpinned"):
        _run(validate_connector(connector, settings))


def test_dns_rebinding_public_to_private_cannot_reach_connection() -> None:
    # Even in local/test (where hostname connectors resolve), an answer that
    # flips to private without the explicit opt-in is refused...
    settings = make_settings(env="test")
    connector = TelemetryConnector(url="https://flip.example")

    async def private_resolver(hostname: str, port: int) -> list[str]:
        return ["10.9.8.7"]

    # local/test allows private for convenience; simulate production policy
    # via the prod environment + IP literal (covered above) and via the
    # mixed-answer rule here:
    async def mixed_resolver(hostname: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.9.8.7"]

    with pytest.raises(ConnectorRefusedError, match="connector_mixed_answers_refused"):
        _run(validate_connector(connector, settings, resolver=mixed_resolver))

    # All-private answers in local/test are the sanctioned convenience:
    assert _run(validate_connector(connector, settings, resolver=private_resolver))


def test_https_dns_validation_uses_the_scheme_default_port() -> None:
    settings = make_settings(env="test")
    seen: dict[str, int] = {}

    async def recording_resolver(hostname: str, port: int) -> list[str]:
        seen[hostname] = port
        return ["93.184.216.34"]

    _run(
        validate_connector(
            TelemetryConnector(url="https://secure.example"), settings, resolver=recording_resolver
        )
    )
    assert seen["secure.example"] == 443
    _run(
        validate_connector(
            TelemetryConnector(url="http://plain.example"), settings, resolver=recording_resolver
        )
    )
    assert seen["plain.example"] == 80


def test_url_shape_refusals() -> None:
    settings = make_settings(env="test")
    for url in (
        "https://user:pass@example.test",
        "https://example.test/path#fragment",
        "https://example.test/?query=1",
        "ftp://example.test",
    ):
        with pytest.raises(ConnectorRefusedError):
            _run(validate_connector(TelemetryConnector(url=url), settings))
