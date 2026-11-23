"""The listener's half of the relay (ADR-0037 §3).

An enrolled agent long-polls `/relay/next` for reads Tracesis wants from the
stores inside its cluster, runs them, and posts the answer to `/relay/result`.
Both routes sit on the mutual-TLS ingest listener and take their identity
from the proof-of-possession principal, never from the body:

- `/relay/next` pops ONLY the queue of the principal's cluster.
- `/relay/result` accepts an answer ONLY for a request issued to the
  principal's cluster. An unknown, expired, already answered or foreign
  request id gets one uniform 404.

The agent pulls; nothing is pushed to it (ADR-0005 still holds).
"""

import json
import time
from typing import Annotated, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from drake_api.agents.identity import AgentPrincipal, authenticate_agent
from drake_api.relay import owner_key, queue_key, reply_key
from drake_api.settings import Settings

router = APIRouter(prefix="/internal/v1/agent/relay", tags=["agent-relay"])

#: A long poll stays open at most this long; the agent's HTTP client waits
#: a little longer than whatever it asked for.
MAX_WAIT_SECONDS = 25

#: An answer is waited for by the API for at most its own timeout; a reply
#: nobody collects disappears.
_REPLY_TTL_SECONDS = 60

#: base64 of the largest body the agent forwards (4 MiB), with room.
_MAX_BODY_B64 = 6 * 1024 * 1024


class NextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wait_seconds: int = Field(default=20, ge=1, le=MAX_WAIT_SECONDS)


class RelayResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    status_code: int | None = Field(default=None, ge=100, le=599)
    content_type: str = Field(default="", max_length=200)
    body_b64: str = Field(default="", max_length=_MAX_BODY_B64)
    error: (
        Literal[
            "unreachable",
            "timeout",
            "too_large",
            "redirect",
            "kind_refused",
            "request_refused",
            "not_configured",
        ]
        | None
    ) = None


def _redis(request: Request) -> aioredis.Redis:
    settings: Settings = request.app.state.settings
    return aioredis.from_url(settings.redis_url)


@router.post("/next")
async def relay_next(
    request: Request,
    body: NextRequest,
    principal: AgentPrincipal = Depends(authenticate_agent),
) -> Response:
    redis = _redis(request)
    try:
        ends = time.monotonic() + body.wait_seconds
        while True:
            remaining = ends - time.monotonic()
            if remaining <= 0:
                return Response(status_code=204)
            popped = await redis.blpop([queue_key(principal.cluster_id)], timeout=remaining)
            if popped is None:
                return Response(status_code=204)
            try:
                item = json.loads(popped[1])
                deadline = float(item["deadline"])
            except (ValueError, KeyError, TypeError):
                continue  # not something the API wrote; drop it
            if deadline <= time.time():
                continue  # nobody is waiting for it any more
            payload = json.dumps(
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "deadline": deadline,
                    "request": item["request"],
                },
                separators=(",", ":"),
            )
            return Response(content=payload, media_type="application/json")
    finally:
        await redis.aclose()


@router.post("/result")
async def relay_result(
    request: Request,
    body: RelayResult,
    principal: AgentPrincipal = Depends(authenticate_agent),
) -> dict[str, str]:
    if body.error is None and body.status_code is None:
        raise HTTPException(status_code=422, detail="status_code or error is required")
    redis = _redis(request)
    try:
        owner = await redis.get(owner_key(body.id))
        owner_id = owner.decode() if isinstance(owner, bytes) else owner
        if owner_id != str(principal.cluster_id):
            raise HTTPException(status_code=404, detail="not found")
        reply = json.dumps(
            {
                "status_code": body.status_code,
                "content_type": body.content_type,
                "body_b64": body.body_b64,
                "error": body.error,
            },
            separators=(",", ":"),
        )
        async with redis.pipeline(transaction=True) as pipe:
            pipe.delete(owner_key(body.id))
            pipe.rpush(reply_key(body.id), reply)
            pipe.expire(reply_key(body.id), _REPLY_TTL_SECONDS)
            await pipe.execute()
    finally:
        await redis.aclose()
    return {"result": "ok"}
