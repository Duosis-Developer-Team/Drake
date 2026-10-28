"""ClickHouse read adapter behind the SSRF boundary.

The same contract as the Prometheus adapter (`telemetry.provider`), because
it is the same risk: a server-owned connector decides where Drake connects,
never a request; the credential is a file reference read at query time;
upstream errors become bounded codes; responses are size-capped and
strictly validated.

What is specific to ClickHouse:

**Values never enter the SQL text.** Every caller-influenced value is a
ClickHouse query parameter (`{name:Type}` in the statement, `param_name` on
the wire), which the server parses as a typed literal. The statement itself
is a constant from `observability.logs`; the only identifier substituted
into it is the database name, which comes from settings and is checked
against a strict identifier shape before use.

**The server enforces the ceilings too.** The reader user's profile caps
execution time, result rows and memory, and is read-only. The settings sent
here can narrow those, never widen them.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from drake_api.settings import ObservabilityStoreConnector, Settings
from drake_api.telemetry.provider import validate_connector

_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class StoreUnavailableError(RuntimeError):
    """Timeout / connection failure / upstream 5xx (typed retryable)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StoreContractError(RuntimeError):
    """The store answered something the adapter contract does not allow."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class QueryResult:
    rows: list[dict[str, Any]]


def safe_identifier(value: str) -> str:
    """A database name fit to place in a statement, or a refusal."""
    if not _IDENTIFIER.match(value):
        raise StoreContractError("store_database_name_refused")
    return value


def _read_password(connector: ObservabilityStoreConnector) -> str:
    if not connector.password_file:
        return ""
    try:
        return Path(connector.password_file).read_text(encoding="utf-8").strip()
    except OSError as error:
        # The path is not echoed: it is configuration, and this code is
        # what a caller would see.
        raise StoreUnavailableError("store_credential_unreadable") from error


class ClickHouseStore:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    @property
    def connector(self) -> ObservabilityStoreConnector | None:
        return self._settings.observability_store

    async def query(
        self,
        statement: str,
        parameters: dict[str, str],
        *,
        max_rows: int,
        timeout_seconds: float,
        correlation_id: str,
    ) -> QueryResult:
        connector = self.connector
        if connector is None:
            raise StoreUnavailableError("store_not_configured")
        base_url = await validate_connector(connector, self._settings)
        timeout_seconds = min(timeout_seconds, connector.timeout_seconds)

        params: dict[str, str] = {f"param_{key}": value for key, value in parameters.items()}
        # Narrowing settings. The reader profile holds the real ceilings; a
        # value above them is refused by the server, not honoured.
        params.update(
            {
                "max_result_rows": str(max_rows),
                "result_overflow_mode": "throw",
                "max_execution_time": str(max(1, int(timeout_seconds))),
                "default_format": "JSON",
            }
        )
        headers = {"X-Correlation-ID": correlation_id}
        if connector.username:
            headers["X-ClickHouse-User"] = connector.username
            headers["X-ClickHouse-Key"] = _read_password(connector)

        timeout = httpx.Timeout(timeout_seconds + 1.0, connect=min(timeout_seconds, 3.0))
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=timeout,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "POST", "/", params=params, content=statement.encode(), headers=headers
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        raise StoreContractError("store_redirect_refused")
                    if response.status_code >= 500:
                        raise StoreUnavailableError("store_upstream_error")
                    if response.status_code in (401, 403):
                        raise StoreUnavailableError("store_auth_refused")
                    if response.status_code != 200:
                        # The statement is ours, so a 4xx is a contract
                        # failure. The body (which may quote the query) is
                        # not read.
                        raise StoreContractError("store_rejected_query")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > _MAX_RESPONSE_BYTES:
                            raise StoreContractError("store_response_too_large")
        except httpx.TimeoutException as error:
            raise StoreUnavailableError("store_timeout") from error
        except httpx.HTTPError as error:
            raise StoreUnavailableError("store_unreachable") from error

        try:
            document = json.loads(bytes(body))
        except ValueError as error:
            raise StoreContractError("store_malformed_response") from error
        if not isinstance(document, dict) or not isinstance(document.get("data"), list):
            raise StoreContractError("store_malformed_response")
        rows = document["data"]
        if not all(isinstance(row, dict) for row in rows):
            raise StoreContractError("store_malformed_response")
        if len(rows) > max_rows:
            raise StoreContractError("store_too_many_rows")
        return QueryResult(rows=rows)
