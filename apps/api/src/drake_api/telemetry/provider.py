"""Prometheus provider adapter behind the SSRF boundary.

``integrations.config_ref`` is only a reference NAME; the server-owned
connector resolver maps it to a base URL from settings (environment /
external-secret backed; dependency-injected fakes in tests). The endpoint
never comes from a request. Raw upstream errors are redacted to bounded
codes; responses are size-capped and strictly validated.
"""

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from drake_api.settings import Settings, TelemetryConnector

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderUnavailableError(RuntimeError):
    """Timeout / connection failure / upstream 5xx (typed retryable)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProviderContractError(RuntimeError):
    """The upstream response violates the adapter contract (fail-closed)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ConnectorRefusedError(RuntimeError):
    """The connector target violates the SSRF boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RangeQueryResult:
    result: list[dict[str, Any]]  # raw matrix entries, validated shape only


def _refuse(condition: bool, code: str) -> None:
    if condition:
        raise ConnectorRefusedError(code)


def _check_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    settings: Settings,
    connector: TelemetryConnector,
) -> bool:
    """Validate one resolved address; returns whether it is private."""
    # Always refused, in every environment:
    _refuse(address.is_link_local, "connector_target_refused")  # incl. 169.254.169.254
    _refuse(address.is_multicast, "connector_target_refused")
    _refuse(address.is_unspecified, "connector_target_refused")
    _refuse(address.is_reserved, "connector_target_refused")
    local_like = settings.env in ("local", "test")
    # Loopback is a local/test convenience only.
    _refuse(address.is_loopback and not local_like, "connector_target_refused")
    if address.is_private and not address.is_loopback:
        # Private targets need the connector's EXPLICIT opt-in outside
        # local/test — presence in the server-owned map is not enough.
        _refuse(not (local_like or connector.allow_private), "connector_private_refused")
        return True
    return False


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def _default_resolver(hostname: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def validate_connector(
    connector: TelemetryConnector, settings: Settings, resolver: Resolver | None = None
) -> str:
    """SSRF boundary for server-owned connectors (never caller input)."""
    parts = urlsplit(connector.url)
    _refuse(parts.scheme not in ("http", "https"), "connector_scheme_refused")
    _refuse(
        parts.scheme == "http" and settings.env not in ("local", "test"),
        "connector_plaintext_refused",
    )
    _refuse(
        parts.username is not None or parts.password is not None, "connector_credentials_refused"
    )
    _refuse(bool(parts.fragment) or bool(parts.query), "connector_url_shape_refused")
    _refuse(not parts.hostname, "connector_host_missing")
    hostname = str(parts.hostname)
    # DNS validation uses the scheme's real default port (443 for https).
    port = parts.port or (443 if parts.scheme == "https" else 80)

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        # IP-literal connectors: no DNS involved, no rebinding surface.
        _check_address(literal, settings, connector)
        return connector.url

    # DNS-name connectors: httpx re-resolves independently of this check, so
    # a hostile DNS answer could flip public→private between validation and
    # connection (rebinding). Until the transport pins the validated address,
    # hostname connectors are FAIL-CLOSED outside local/test — a documented
    # deployment blocker for real provider onboarding, not an accepted risk.
    _refuse(settings.env not in ("local", "test"), "connector_hostname_unpinned")

    try:
        addresses = await (resolver or _default_resolver)(hostname, port)
    except socket.gaierror as error:
        raise ConnectorRefusedError("connector_unresolvable") from error
    _refuse(not addresses, "connector_unresolvable")
    verdicts = [
        _check_address(ipaddress.ip_address(address), settings, connector) for address in addresses
    ]
    # Mixed public/private answer sets are a rebinding smell: refuse outright.
    _refuse(any(verdicts) and not all(verdicts), "connector_mixed_answers_refused")
    return connector.url


class PrometheusAdapter:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    def resolve_connector(self, config_ref: str) -> TelemetryConnector | None:
        return self._settings.telemetry_connectors.get(config_ref)

    async def query_range(
        self,
        connector: TelemetryConnector,
        query: str,
        *,
        start: int,
        end: int,
        step_seconds: int,
        timeout_seconds: float,
        correlation_id: str,
    ) -> RangeQueryResult:
        base_url = await validate_connector(connector, self._settings)
        timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0))
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=timeout,
                follow_redirects=False,  # redirects are never followed
                transport=self._transport,
            ) as client:
                # Streaming read: the size limit is enforced chunk by chunk as
                # a real memory/network budget — the moment the cap is crossed
                # the stream is closed, without buffering the full payload.
                async with client.stream(
                    "POST",
                    "/api/v1/query_range",
                    data={
                        "query": query,
                        "start": str(start),
                        "end": str(end),
                        "step": str(step_seconds),
                    },
                    headers={"X-Correlation-ID": correlation_id},
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        raise ProviderContractError("provider_redirect_refused")
                    if response.status_code >= 500:
                        raise ProviderUnavailableError("provider_upstream_error")
                    if response.status_code != 200:
                        # 4xx from Prometheus (bad query etc.) — our compiler
                        # produced it, so this is a contract failure, never
                        # echoed to the caller. The body is not read.
                        raise ProviderContractError("provider_rejected_query")
                    media_type = (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    )
                    if media_type != "application/json":
                        # Content type is never echoed to responses or logs.
                        raise ProviderContractError("provider_unexpected_content_type")

                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > _MAX_RESPONSE_BYTES:
                            raise ProviderContractError("provider_response_too_large")
        except httpx.TimeoutException as error:
            # Covers connect AND mid-stream read timeouts; the context
            # managers close the stream/client on the way out.
            raise ProviderUnavailableError("provider_timeout") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailableError("provider_unreachable") from error

        try:
            document = json.loads(bytes(body))
        except ValueError as error:
            raise ProviderContractError("provider_malformed_response") from error
        if not isinstance(document, dict):
            raise ProviderContractError("provider_malformed_response")
        if document.get("status") == "error":
            # Upstream error strings are redacted to a bounded code.
            raise ProviderContractError("provider_query_error")
        data = document.get("data")
        if not isinstance(data, dict) or data.get("resultType") != "matrix":
            raise ProviderContractError("provider_malformed_response")
        result = data.get("result")
        if not isinstance(result, list):
            raise ProviderContractError("provider_malformed_response")
        for entry in result:
            if not isinstance(entry, dict) or not isinstance(entry.get("metric"), dict):
                raise ProviderContractError("provider_malformed_response")
            if not isinstance(entry.get("values"), list):
                raise ProviderContractError("provider_malformed_response")
        return RangeQueryResult(result=result)
