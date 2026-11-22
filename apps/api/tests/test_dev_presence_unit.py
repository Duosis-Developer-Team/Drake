"""Presence (who is at work where now) is one grouped read under the row ceiling."""

from datetime import UTC, datetime
from typing import Any

from drake_api.dev_activity import collector


def test_the_presence_read_is_grouped_bounded_and_bound() -> None:
    sql = collector.presence_statement("otel")
    assert "FROM otel.otel_logs" in sql
    assert "GROUP BY project_tag, vcs, vcs_owner, vcs_name, vcs_url, identity" in sql
    assert "{max_rows:UInt32}" in sql and "{start:DateTime64(9, 'UTC')}" in sql
    # The reader refuses max_result_rows above 1000; presence never asks for more.
    assert collector.MAX_USAGE_ROWS <= 1000


def test_store_times_are_read_to_the_microsecond() -> None:
    assert collector._store_time("2026-11-23 09:15:02.123456789") == datetime(
        2026, 11, 23, 9, 15, 2, 123456, tzinfo=UTC
    )
    assert collector._store_time("2026-11-23 09:15:02") == datetime(
        2026, 11, 23, 9, 15, 2, tzinfo=UTC
    )
    assert collector._store_time("") is None
    assert collector._store_time("yesterday") is None


class _Failing:
    async def query(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("store down")


async def test_a_failed_read_is_nobody_here() -> None:
    found = await collector.read_presence(
        None,  # type: ignore[arg-type]  # never reached: the read fails first
        _Failing(),
        database="otel",
        now=datetime.now(UTC),
    )
    assert found == []
