"""E2E telemetry integration configuration (local/test only, idempotent).

Marks project alpha's prometheus integration as configured with the
`e2e-prometheus` connector reference (resolved server-side to the flaky
proxy in front of the local fixture Prometheus). Beta's integration stays
honestly not_configured — that IS the E2E not-configured scenario.
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    env = os.environ.get("DRAKE_ENV", "local")
    if env not in ("local", "test"):
        raise RuntimeError("e2e telemetry configuration is local/test only")
    engine = create_async_engine(os.environ["DRAKE_DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE integrations
                    SET config_ref = 'e2e-prometheus',
                        configuration_state = 'configured',
                        updated_at = now()
                    WHERE integration_type = 'prometheus'
                      AND scope_id = (
                        SELECT scope_id FROM projects WHERE project_key = 'alpha'
                      )
                    """
                )
            )
    finally:
        await engine.dispose()
    sys.stdout.write("e2e telemetry configuration applied\n")


if __name__ == "__main__":
    asyncio.run(main())
