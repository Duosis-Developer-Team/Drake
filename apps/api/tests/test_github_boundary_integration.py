"""Inbound byte limits, envelope budget, ownership, and scope isolation.

CTO fix-gate regressions §5 to §8. The bodies here are driven through the
ASGI `receive` channel directly so the chunked and lying-`Content-Length`
cases are real transport behaviour rather than something the test client
smooths over.
"""

import json as jsonlib
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app import webhook
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import build_users, grant, login_all, make_role  # noqa: F401
from test_github_integration import (  # noqa: F401 - fixtures are used by name
    HERMES_ID,
    INSTALLATION_ID,
    LOGISLOT_ID,
    WEBHOOK_SECRET,
    _seed_admin,
    deliver,
    github_harness,
    installation_payload,
    sign,
    webhook_headers,
)

pytestmark = pytest.mark.integration


async def _other_scope(engine: AsyncEngine) -> uuidlib.UUID:
    """A second scope to test isolation against, created explicitly.

    These tests are about "not the caller's scope", so the second scope is
    part of the test's own setup rather than something borrowed from the
    catalog fixtures.
    """
    async with engine.begin() as connection:
        existing = (
            await connection.execute(
                text(
                    "SELECT id FROM scopes WHERE scope_type = 'project' "
                    "AND external_ref = 'isolation-probe'"
                )
            )
        ).first()
        if existing is not None:
            return uuidlib.UUID(str(existing[0]))
        created = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref) "
                    "VALUES ('project', 'isolation-probe') RETURNING id"
                )
            )
        ).scalar_one()
    return uuidlib.UUID(str(created))


async def _send_chunked(
    harness: Any,
    chunks: list[bytes],
    *,
    declared_length: int | None,
    event: str = "installation",
    sign_body: bytes | None = None,
) -> tuple[int, int]:
    """Drive the ASGI app directly so chunk boundaries are real.

    Returns (status_code, bytes_actually_offered_to_the_app) — the second
    value is how much of the body the endpoint pulled off the wire, which
    is what "at most limit + 1 in memory" actually means.
    """
    body = b"".join(chunks)
    app = harness.app
    delivered = {"bytes": 0}
    remaining = list(chunks)

    headers = [
        (b"content-type", b"application/json"),
        (b"x-github-event", event.encode()),
        (b"x-github-delivery", str(uuidlib.uuid4()).encode()),
        (b"x-hub-signature-256", sign(body if sign_body is None else sign_body).encode()),
    ]
    if declared_length is not None:
        headers.append((b"content-length", str(declared_length).encode()))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/integrations/github/webhook",
        "raw_path": b"/v1/integrations/github/webhook",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    async def receive() -> dict[str, Any]:
        if remaining:
            chunk = remaining.pop(0)
            delivered["bytes"] += len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": bool(remaining)}
        return {"type": "http.request", "body": b"", "more_body": False}

    status = {"code": 0}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status["code"] = int(message["status"])

    await app(scope, receive, send)
    return status["code"], delivered["bytes"]


def _padded_payload(target_bytes: int) -> bytes:
    """A syntactically valid installation payload padded to a size."""
    payload = installation_payload()
    body = jsonlib.dumps(payload)
    padding = max(0, target_bytes - len(body) - 20)
    payload["_pad"] = "x" * padding
    return jsonlib.dumps(payload).encode()


# --- §5 streaming inbound limit -----------------------------------------


async def test_body_under_the_limit_is_accepted_across_many_chunks(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    limit = harness.settings.github_webhook_max_body_bytes

    body = _padded_payload(limit // 2)
    chunks = [body[index : index + 512] for index in range(0, len(body), 512)]
    assert len(chunks) > 1, "the point of this test is multiple chunks"
    status, _read = await _send_chunked(harness, chunks, declared_length=len(body))
    assert status == 202


async def test_body_exactly_at_the_limit_is_accepted(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    limit = harness.settings.github_webhook_max_body_bytes

    # Grow the padding to land exactly on the ceiling rather than guessing
    # how the serializer spaces its output.
    payload = installation_payload()
    payload["_pad"] = ""
    overhead = len(jsonlib.dumps(payload).encode())
    payload["_pad"] = "x" * (limit - overhead)
    body = jsonlib.dumps(payload).encode()
    assert len(body) == limit, f"padded to {len(body)}, wanted exactly {limit}"
    status, _read = await _send_chunked(harness, [body], declared_length=len(body))
    assert status == 202, f"a body of exactly {len(body)}/{limit} bytes must be accepted"


async def test_one_byte_over_the_limit_is_refused(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    limit = harness.settings.github_webhook_max_body_bytes

    body = b"x" * (limit + 1)
    status, _read = await _send_chunked(harness, [body], declared_length=len(body))
    assert status == 413


async def test_large_chunked_body_without_content_length_is_bounded(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """No Content-Length at all: the streaming limit is the only defence."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    limit = harness.settings.github_webhook_max_body_bytes

    chunk = b"y" * 4096
    chunks = [chunk] * ((limit // 4096) + 8)
    status, read_bytes = await _send_chunked(harness, chunks, declared_length=None)
    assert status == 413
    assert read_bytes <= limit + 4096, (
        f"read {read_bytes} bytes for a {len(chunks) * 4096}-byte body: "
        "the endpoint buffered far past its own ceiling"
    )


async def test_understated_content_length_cannot_smuggle_a_large_body(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A lying Content-Length must not become the security boundary."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    limit = harness.settings.github_webhook_max_body_bytes

    chunk = b"z" * 4096
    chunks = [chunk] * ((limit // 4096) + 8)
    status, read_bytes = await _send_chunked(harness, chunks, declared_length=10)
    assert status == 413, "the declared length is a hint, not the limit"
    assert read_bytes <= limit + 4096


async def test_streamed_body_preserves_the_exact_bytes_for_hmac(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Reassembly must reproduce the signed bytes exactly, split or not."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)

    body = jsonlib.dumps(installation_payload()).encode()
    # One byte per chunk: the most hostile reassembly the endpoint can face.
    status, _read = await _send_chunked(
        harness, [body[i : i + 1] for i in range(len(body))], declared_length=len(body)
    )
    assert status == 202, "a correct signature must survive arbitrary chunking"

    # And a single mutated byte still fails, so this is not accidentally lax.
    mutated = bytearray(body)
    mutated[-3] ^= 0x01
    app_status, _ = await _send_chunked(
        harness, [bytes(mutated)], declared_length=len(mutated), sign_body=body
    )
    assert app_status == 401, "reassembled bytes are what the HMAC must cover"


# --- §6 envelope byte budget vs the DB constraint ------------------------


def _hostile_repositories(count: int) -> list[dict[str, Any]]:
    """Maximum-length fields, so the budget is tested at its worst case."""
    owner = "Duosis-Developer-Team"
    return [
        {
            "id": 900_000 + index,
            "node_id": "R_" + "k" * 126,
            "name": "r" * 255,
            "full_name": f"{owner}/" + "r" * (255 - len(owner) - 1),
            "private": True,
        }
        for index in range(count)
    ]


async def test_maximum_envelope_fits_the_database_constraint(engine: AsyncEngine) -> None:
    """The builder's worst case must fit what the schema accepts.

    Measured with the real `pg_column_size` on jsonb, not an estimate of
    the serialized text.
    """
    payload = installation_payload(repositories=_hostile_repositories(100))
    envelope = webhook.build_envelope("installation", payload)
    stored = envelope.as_json()

    async with engine.connect() as connection:
        size = int(
            (
                await connection.execute(
                    text("SELECT pg_column_size(CAST(:doc AS jsonb))"),
                    {"doc": jsonlib.dumps(stored)},
                )
            ).scalar_one()
        )
    assert size <= 8192, f"envelope is {size} bytes; the column constraint is 8192"


async def test_oversized_installation_event_is_stored_as_truncated_not_rejected(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A big but legitimate webhook must not 500, and must not lie."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())
    payload = installation_payload(repositories=_hostile_repositories(100))

    async with harness.api_client() as client:
        response = await deliver(client, "installation", payload, delivery)
    assert response.status_code == 202, response.text

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT envelope, pg_column_size(envelope) "
                    "FROM github_webhook_deliveries WHERE delivery_id = :id"
                ),
                {"id": delivery},
            )
        ).one()
    stored = row[0] if isinstance(row[0], dict) else jsonlib.loads(str(row[0]))
    assert int(row[1]) <= 8192

    # Silent loss is the failure mode: a partial list must announce itself.
    assert stored["truncated"] is True
    assert stored["observed_repository_count"] == 100
    assert stored["reconciliation_required"] is True
    assert len(stored["repositories"]) < 100
    assert len(stored["repositories"]) > 0, "some identities must survive to act on"


async def test_a_normal_installation_event_is_not_marked_truncated(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), delivery)

    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text("SELECT envelope FROM github_webhook_deliveries WHERE delivery_id = :id"),
                {"id": delivery},
            )
        ).scalar_one()
    envelope = stored if isinstance(stored, dict) else jsonlib.loads(str(stored))
    assert envelope["truncated"] is False
    assert envelope["reconciliation_required"] is False
    assert envelope["observed_repository_count"] == len(envelope["repositories"]) == 2


# --- §7 ownership and installation relationship, fail-closed -------------


@pytest.mark.parametrize(
    ("mutation", "label"),
    [
        (lambda p: p["installation"].pop("account", None), "account object missing"),
        (lambda p: p["installation"].update({"account": {}}), "account login missing"),
        (lambda p: p["installation"].update({"account": {"login": ""}}), "account login empty"),
        (
            lambda p: p["installation"].update({"account": {"login": "someone-else"}}),
            "foreign account",
        ),
        (lambda p: p["installation"].pop("id", None), "installation id missing"),
        (lambda p: p["installation"].update({"id": "55501"}), "installation id not an int"),
    ],
)
async def test_missing_or_foreign_ownership_is_refused(
    engine: AsyncEngine, tmp_path: Path, mutation: Any, label: str
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    payload = installation_payload()
    mutation(payload)

    async with harness.api_client() as client:
        response = await deliver(client, "installation", payload, str(uuidlib.uuid4()))
    assert response.status_code == 401, f"{label} must be refused: {response.text}"

    # A refused delivery does no domain work and leaves no row behind.
    async with engine.connect() as connection:
        assert (
            int(
                (
                    await connection.execute(text("SELECT count(*) FROM github_repositories"))
                ).scalar_one()
            )
            == 0
        )


async def test_a_repository_owned_by_another_organization_is_refused(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    payload = installation_payload(
        repositories=[
            {
                "id": 777001,
                "node_id": "R_foreign",
                "name": "trophy",
                "full_name": "another-org/trophy",
                "private": True,
            }
        ]
    )
    async with harness.api_client() as client:
        response = await deliver(client, "installation", payload, str(uuidlib.uuid4()))
    assert response.status_code == 401

    async with engine.connect() as connection:
        assert (
            int(
                (
                    await connection.execute(text("SELECT count(*) FROM github_repositories"))
                ).scalar_one()
            )
            == 0
        )


async def test_a_mixed_owner_repository_list_is_refused_entirely(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """One foreign entry poisons the batch; partial acceptance is not safe."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    payload = installation_payload(
        repositories=[
            {
                "id": HERMES_ID,
                "node_id": "R_hermes",
                "name": "Hermes",
                "full_name": "Duosis-Developer-Team/Hermes",
                "private": True,
            },
            {
                "id": 777002,
                "node_id": "R_foreign",
                "name": "smuggled",
                "full_name": "attacker-org/smuggled",
                "private": True,
            },
        ]
    )
    async with harness.api_client() as client:
        response = await deliver(client, "installation", payload, str(uuidlib.uuid4()))
    assert response.status_code == 401

    async with engine.connect() as connection:
        rows = int(
            (
                await connection.execute(text("SELECT count(*) FROM github_repositories"))
            ).scalar_one()
        )
    assert rows == 0, "not even the legitimate half may be written"


async def test_a_repository_without_a_resolvable_owner_is_refused(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    payload = installation_payload(
        repositories=[{"id": 777003, "node_id": "R_x", "name": "orphan", "private": True}]
    )
    async with harness.api_client() as client:
        response = await deliver(client, "installation", payload, str(uuidlib.uuid4()))
    # Either the entry is dropped as unidentifiable and the event carries no
    # repositories, or the delivery is refused — never a silent write.
    assert response.status_code in (202, 401)
    async with engine.connect() as connection:
        assert (
            int(
                (
                    await connection.execute(text("SELECT count(*) FROM github_repositories"))
                ).scalar_one()
            )
            == 0
        )


async def test_installation_bound_to_another_scope_is_refused(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """An event that contradicts the persisted installation/scope link."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)

    async with harness.api_client() as client:
        first = await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        assert first.status_code == 202

        # Move the installation to a different scope behind Drake's back.
        other_scope = await _other_scope(engine)
        async with engine.begin() as connection:
            # The composite FK binds a repository's scope to its
            # installation's, so a re-scoping moves both together.
            await connection.execute(
                text("UPDATE github_installations SET scope_id = :scope WHERE external_id = :ext"),
                {"scope": other_scope, "ext": INSTALLATION_ID},
            )
            await connection.execute(
                text("UPDATE github_repositories SET scope_id = :scope"),
                {"scope": other_scope},
            )

        refused = await deliver(
            client, "installation", installation_payload(), str(uuidlib.uuid4())
        )
    assert refused.status_code == 401


# --- §8 delivery metadata scope isolation --------------------------------


async def test_delivery_metadata_is_filtered_to_the_callers_scopes(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Managing SOMETHING must not mean reading EVERY tenant's deliveries."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)

    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        await harness.login(client, "user-owner")
        owner_view = await client.get("/v1/integrations/github/webhook-deliveries")
        assert owner_view.status_code == 200
        assert len(owner_view.json()["deliveries"]) >= 1

    # A principal holding integration.manage on a DIFFERENT scope only.
    other_scope = await _other_scope(engine)
    await login_all(harness, ["user-scoped"])
    async with engine.begin() as connection:
        role_id = (
            await connection.execute(
                text("INSERT INTO roles (name, description) VALUES (:n, '') RETURNING id"),
                {"n": "Scoped Integrator S5A"},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO role_permissions (role_id, permission_key) "
                "VALUES (:role, 'integration.manage')"
            ),
            {"role": role_id},
        )
        identity_id = (
            await connection.execute(
                text("SELECT id FROM identities WHERE subject = :s"),
                {"s": "user-scoped"},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO grants (identity_id, role_id, scope_id) "
                "VALUES (:identity, :role, :scope)"
            ),
            {"identity": identity_id, "role": role_id, "scope": other_scope},
        )

    async with harness.api_client() as scoped_client:
        await harness.login(scoped_client, "user-scoped")
        scoped = await scoped_client.get("/v1/integrations/github/webhook-deliveries")

    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["deliveries"] == [], (
        "delivery metadata belonging to another scope must not be readable"
    )
