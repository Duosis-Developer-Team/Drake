"""Integration observation write path (bounded, safe, application-level).

Called only when a REAL provider call happened — cache hits never fake a
provider success. Updates the projection columns of the integration row:
states, timestamps, a bounded machine-readable error code (validated by
the same validator the DB CHECK backs), and the row version. Raw provider
errors/bodies/URLs/credentials never reach any column.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.catalog.service import CatalogService

# A success older than this window turns later failures into `stale` —
# the projection stops claiming a merely-degraded source.
FRESHNESS_WINDOW_SECONDS = 300


async def record_provider_observation(
    engine: AsyncEngine,
    integration_id: str,
    *,
    outcome: Literal["success", "failure"],
    error_code: str | None = None,
) -> None:
    safe_code = CatalogService.validate_error_code(error_code)
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        if outcome == "success":
            await connection.execute(
                text(
                    """
                    UPDATE integrations
                    SET last_sync_attempt_at = :now,
                        last_success_at = :now,
                        observed_state = 'ok',
                        last_error_code = NULL,
                        version = version + 1,
                        updated_at = now()
                    WHERE id = :id
                    """
                ),
                {"id": integration_id, "now": now},
            )
            return
        stale_before = now - timedelta(seconds=FRESHNESS_WINDOW_SECONDS)
        await connection.execute(
            text(
                """
                UPDATE integrations
                SET last_sync_attempt_at = :now,
                    observed_state = CASE
                        WHEN last_success_at IS NULL OR last_success_at < :stale_before
                            THEN 'stale'
                        ELSE 'degraded'
                    END,
                    last_error_code = :code,
                    version = version + 1,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": integration_id, "now": now, "stale_before": stale_before, "code": safe_code},
        )
