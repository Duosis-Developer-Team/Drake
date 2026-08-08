"""The connector ingest endpoint.

Authenticated as a CONNECTOR, not as a user: a backup reporter has no
session, no scope grants, and no business reading anything. It presents a
connector key and an HMAC signature, and the registry decides which
project its evidence may touch.

Rejections are bounded codes. A provider's own error text is never echoed
back, logged, or stored — that is where a connection string ends up.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from drake_api.db import get_engine
from drake_api.protection import ingest
from drake_api.protection.service import evaluate_policy
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.protection.ingest")

router = APIRouter(prefix="/v1", tags=["protection-ingest"])


async def _authenticate(request: Request) -> tuple[str, Any, bytes]:
    settings: Settings = request.app.state.settings
    body = await request.body()
    if len(body) > settings.protection_max_body_bytes:
        raise HTTPException(status_code=413, detail="payload too large")
    try:
        connector_key, connector = ingest.resolve_connector(
            settings, request.headers.get(ingest.CONNECTOR_HEADER)
        )
        ingest.verify_signature(
            connector,
            secret=ingest.load_signing_secret(connector),
            timestamp=request.headers.get(ingest.TIMESTAMP_HEADER),
            signature=request.headers.get(ingest.SIGNATURE_HEADER),
            body=body,
            now=datetime.now(UTC),
        )
    except ingest.IngestRejectedError as error:
        # One status for every authentication failure: distinguishing
        # "unknown connector" from "bad signature" tells a prober which
        # half to keep working on.
        raise HTTPException(status_code=401, detail="unauthorized") from error
    return connector_key, connector, body


@router.post("/protection/ingest/events", status_code=202)
async def ingest_event(request: Request) -> dict[str, Any]:
    """Accept one signed protection event."""
    connector_key, _connector, body = await _authenticate(request)
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)

    import json

    try:
        envelope = json.loads(body)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid_payload") from error
    if not isinstance(envelope, dict):
        raise HTTPException(status_code=422, detail="invalid_payload")

    try:
        result = await ingest.apply_event(
            engine, connector_key, envelope, now=datetime.now(UTC)
        )
    except ingest.IngestRejectedError as error:
        raise HTTPException(status_code=422, detail=error.code) from error

    # Re-evaluate immediately so the Protection Center reflects the new
    # evidence without waiting for a timer. Evaluation touches no network.
    if result.outcome == "applied":
        await _reevaluate(engine, settings, connector_key)
    return {"outcome": result.outcome}


@router.post("/protection/ingest/snapshots", status_code=202)
async def snapshot(request: Request) -> dict[str, Any]:
    """Reconciliation: `begin` → `page` → `complete`.

    An open snapshot changes nothing on its own. Only `complete` makes its
    contents current, so an interrupted reconciliation leaves the last good
    projection standing rather than half-replacing it.
    """
    connector_key, _connector, body = await _authenticate(request)
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)

    import json

    try:
        payload = json.loads(body)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid_payload") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="invalid_payload")

    kind = str(payload.get("kind") or "")
    if kind == "begin":
        snapshot_id = await ingest.begin_snapshot(engine, connector_key)
        return {"snapshot_id": str(snapshot_id)}

    raw_id = payload.get("snapshot_id")
    try:
        snapshot_id = uuid.UUID(str(raw_id))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="invalid_snapshot") from error

    if kind == "page":
        events = payload.get("events")
        if not isinstance(events, list) or len(events) > settings.protection_max_page_records:
            raise HTTPException(status_code=422, detail="invalid_page")
        applied = await ingest.snapshot_page(
            engine, connector_key, snapshot_id, events, now=datetime.now(UTC)
        )
        return {"applied": applied}

    if kind == "complete":
        if not await ingest.complete_snapshot(engine, snapshot_id):
            raise HTTPException(status_code=409, detail="snapshot_not_open")
        await _reevaluate(engine, settings, connector_key)
        return {"completed": True}

    raise HTTPException(status_code=422, detail="unknown_kind")


async def _reevaluate(engine: Any, settings: Settings, connector_key: str) -> None:
    """Re-evaluate this connector's policies after new evidence."""
    from sqlalchemy import text

    async with engine.connect() as connection:
        policy_ids = [
            uuid.UUID(str(row[0]))
            for row in (
                await connection.execute(
                    text(
                        "SELECT id FROM backup_policies WHERE connector_key = :connector "
                        "AND enabled LIMIT 100"
                    ),
                    {"connector": connector_key},
                )
            ).all()
        ]
    for policy_id in policy_ids:
        try:
            await evaluate_policy(engine, settings, policy_id)
        except Exception:
            logger.warning("protection evaluation failed for one policy")
