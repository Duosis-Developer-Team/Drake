"""From an empty Protection screen to judged evidence.

The reporter CLI's events go through the real signed ingest endpoint; the
evaluator seeds declared policies and judges them on its own; the status
endpoint says, per declared reporter, what is missing — to people who may
see the project, and without any secret.
"""

import argparse
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import redis.asyncio as aioredis
from drake_api.protection import report_cli, setup
from drake_api.protection.connectors import seed_policies
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import login_all
from test_protection_integration import CONNECTOR, connector_settings, seed_project
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]


@pytest.fixture
async def redis() -> Any:
    client = aioredis.from_url(require_it_settings().redis_url)
    await client.delete(setup.LEASE_KEY)
    yield client
    await client.delete(setup.LEASE_KEY)
    await client.aclose()


async def backup_state(engine: AsyncEngine, policy_key: str) -> tuple[str, str] | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT e.backup_state, e.overall_state FROM protection_evaluations e "
                    "JOIN backup_policies p ON p.id = e.policy_id "
                    "WHERE p.policy_external_key = :k ORDER BY e.evaluated_for DESC LIMIT 1"
                ),
                {"k": policy_key},
            )
        ).first()
    return (str(row[0]), str(row[1])) if row else None


async def test_the_reporter_cli_speaks_the_ingest_contract(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    await seed_project(engine, "hermes", "dev")
    settings, secret = connector_settings(tmp_path)
    await seed_policies(engine, connector_key=CONNECTOR)
    dump = tmp_path / "hermes-core-20261121.dump"
    dump.write_bytes(b"PGDMP" + b"\x00" * 4096)
    args = argparse.Namespace(
        policy="hermes-dev-core",
        run_id="",
        artifact=str(dump),
        started=datetime.now(UTC) - timedelta(minutes=5),
        status="succeeded",
        error_code="",
        site="onedrive-primary",
        offsite=True,
        encrypted=False,
    )
    checksum, size = report_cli.sha256_of(dump)
    harness = build_harness(settings)
    async with harness.api_client() as client:
        for event in report_cli.events(args, checksum, size, datetime.now(UTC)):
            body = json.dumps(event, separators=(",", ":")).encode()
            stamp = str(int(time.time()))
            response = await client.post(
                "/v1/protection/ingest/events",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Drake-Protection-Connector": CONNECTOR,
                    "X-Drake-Protection-Timestamp": stamp,
                    "X-Drake-Protection-Signature": report_cli.sign(secret, stamp, body),
                },
            )
            assert response.status_code == 202, (event["type"], response.text)
        # A wrong secret is refused like any other bad signature.
        bad = await client.post(
            "/v1/protection/ingest/events",
            content=b"{}",
            headers={
                "X-Drake-Protection-Connector": CONNECTOR,
                "X-Drake-Protection-Timestamp": str(int(time.time())),
                "X-Drake-Protection-Signature": report_cli.sign(b"wrong", "0", b"{}"),
            },
        )
        assert bad.status_code == 401
    state = await backup_state(engine, "hermes-dev-core")
    assert state is not None and state[0] == "protected", state
    # The artifact is named by its file name only: no directory reaches the server.
    async with engine.connect() as connection:
        keys = [
            r[0]
            for r in (
                await connection.execute(text("SELECT artifact_external_key FROM backup_artifacts"))
            ).all()
        ]
    assert dump.name in keys and all("/" not in k for k in keys)


async def test_the_evaluator_seeds_declared_policies_and_judges_silence(
    engine: AsyncEngine, tmp_path: Path, redis: Any
) -> None:
    await seed_project(engine, "hermes", "dev")
    settings, _secret = connector_settings(tmp_path)
    judged = await setup.run_once(engine, settings, redis)
    assert judged >= 2  # hermes-dev-core and hermes-dev-auth, seeded on the way
    # Nothing ever reported: not protected, and said so.
    state = await backup_state(engine, "hermes-dev-core")
    assert state is not None and state[0] != "protected"


async def test_the_status_says_what_is_missing_to_who_may_see_it(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    await seed_project(engine, "hermes", "dev")
    settings, _secret = connector_settings(tmp_path)
    harness = build_harness(settings)
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-nobody", user_type("user-nobody", "Nobody", "user-nobody@example.test")
    )
    await login_all(harness, ["user-nobody"])
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        body = (await client.get("/v1/protection/connectors")).json()
    hermes = next(r for r in body["reporters"] if r["connector_key"] == CONNECTOR)
    assert hermes["registered"] is True and hermes["secret_present"] is True
    assert hermes["state"] == "waiting" and hermes["last_seen_at"] is None
    assert {p["policy_key"] for p in hermes["policies"]} >= {"hermes-dev-core"}
    assert body["signing"]["endpoint"] == "/v1/protection/ingest/events"
    # A reporter for a project not in the catalog is not listed at all.
    assert all(r["project_key"] in {"hermes"} for r in body["reporters"])
    text_body = json.dumps(body)
    assert str(tmp_path) not in text_body and "signing_secret" not in text_body

    async with harness.api_client() as client:
        await harness.login(client, "user-nobody")
        assert (await client.get("/v1/protection/connectors")).json()["reporters"] == []


def test_the_cli_names_the_run_after_the_policy_and_start() -> None:
    args = argparse.Namespace(
        policy="p",
        run_id="",
        artifact="/var/backups/x/db.dump",
        started=datetime(2026, 11, 21, 2, 0, tzinfo=UTC),
        status="failed",
        error_code="disk_full",
        site="",
        offsite=False,
        encrypted=False,
    )
    [only] = report_cli.events(args, "", 0, datetime(2026, 11, 21, 2, 5, tzinfo=UTC))
    assert only["data"]["run_id"] == "p-20261121T020000Z"
    assert only["data"]["status"] == "failed" and only["data"]["error_code"] == "disk_full"
    assert uuid.UUID(only["id"].removeprefix("evt-"))
