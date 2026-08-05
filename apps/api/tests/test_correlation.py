"""Correlation ID contract: safe inbound IDs survive, unsafe ones are replaced."""

import re

from httpx import AsyncClient

UUID_HEX = re.compile(r"^[0-9a-f]{32}$")


async def test_valid_inbound_correlation_id_is_preserved(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Correlation-ID": "req-abc.123_456"})
    assert response.headers["X-Correlation-ID"] == "req-abc.123_456"


async def test_missing_correlation_id_is_generated(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert UUID_HEX.fullmatch(response.headers["X-Correlation-ID"])


async def test_unsafe_correlation_id_is_replaced(client: AsyncClient) -> None:
    hostile = 'bad value with spaces and "quotes"'
    response = await client.get("/health/live", headers={"X-Correlation-ID": hostile})
    echoed = response.headers["X-Correlation-ID"]
    assert echoed != hostile
    assert UUID_HEX.fullmatch(echoed)


async def test_too_short_correlation_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Correlation-ID": "abc"})
    assert UUID_HEX.fullmatch(response.headers["X-Correlation-ID"])
