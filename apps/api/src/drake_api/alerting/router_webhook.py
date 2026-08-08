"""The Alertmanager webhook receiver.

Authenticated as an INTEGRATION, not as a user: Alertmanager has no
session, no scope grants, and no business reading anything. It presents an
opaque key in the path and a bearer token in the `Authorization` header,
and the server-side registry decides which project its alerts may resolve
into.

Native Alertmanager does not sign its bodies. Rather than pretend it does —
accepting a signature header Alertmanager never sends would be inventing a
guarantee, and any client could forge it — the guarantee here is a bearer
token over TLS plus a fully idempotent projection: the same payload
delivered any number of times produces one alert, one event, one incident
and one notification plan.

Rejections are bounded codes with a uniform status. Distinguishing "unknown
integration" from "bad token" tells a prober which half to keep working on.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from drake_api.alerting import ingest
from drake_api.alerting.model import IngestRejectedError, normalize_delivery
from drake_api.db import get_engine
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.alerting.webhook")

router = APIRouter(tags=["alerting-webhook"])


@router.post("/webhooks/alertmanager/{integration_key}", status_code=202)
async def receive(integration_key: str, request: Request) -> dict[str, Any]:
    """Accept one Alertmanager notification.

    `202` always, for anything Drake accepted: Alertmanager treats a
    non-2xx as a failure and retries, and a retry storm caused by an alert
    Drake could not map would be Drake making an outage worse.
    """
    settings: Settings = request.app.state.settings
    body = await request.body()
    if len(body) > settings.alertmanager_max_body_bytes:
        raise HTTPException(status_code=413, detail="payload too large")

    try:
        config_ref, integration = ingest.resolve_integration(settings, integration_key)
        ingest.verify_bearer(integration, request.headers.get("authorization"))
    except IngestRejectedError as error:
        # One status for every authentication failure.
        raise HTTPException(status_code=401, detail="unauthorized") from error

    try:
        payload = json.loads(body)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid_payload") from error

    try:
        delivery = normalize_delivery(payload, body)
    except IngestRejectedError as error:
        raise HTTPException(status_code=422, detail=error.code) from error

    engine = get_engine(settings)
    resolved = await ingest.ensure_integration_row(engine, config_ref, integration.project_key)
    if resolved is None:
        # The registry names a project the catalog does not have. A
        # configuration error, and not one an alert payload can fix.
        raise HTTPException(status_code=409, detail="integration_project_unknown")
    integration_id, project_id = resolved

    outcome = await ingest.apply_delivery(
        engine,
        integration_id=integration_id,
        project_id=project_id,
        project_key=integration.project_key,
        delivery=delivery,
        now=datetime.now(UTC),
    )
    # Counts only. Never an alert name, a label, a fingerprint or anything
    # that would let a caller confirm what Drake knows about an estate.
    return {
        "accepted": outcome.accepted,
        "rejected": outcome.rejected,
        "unmapped": outcome.unmapped,
        "duplicate": outcome.duplicate,
        "truncated": delivery.truncated_alerts > 0,
    }
