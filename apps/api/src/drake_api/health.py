"""Liveness and readiness endpoints.

``/health/live`` answers purely from the process — no dependency checks.
``/health/ready`` evaluates PostgreSQL and Redis separately and never reports
a dependency as healthy when it is not.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from drake_api.db import check_database
from drake_api.redis import check_redis
from drake_api.settings import Settings

router = APIRouter(prefix="/health", tags=["health"])

ComponentStatus = Literal["ok", "unavailable"]


class LiveResponse(BaseModel):
    status: Literal["alive"] = "alive"


class ReadyComponents(BaseModel):
    database: ComponentStatus
    redis: ComponentStatus


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    components: ReadyComponents


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse()


@router.get("/ready", responses={503: {"model": ReadyResponse}})
async def ready(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    database_ok, redis_ok = await asyncio.gather(check_database(settings), check_redis(settings))
    body = ReadyResponse(
        status="ready" if database_ok and redis_ok else "degraded",
        components=ReadyComponents(
            database="ok" if database_ok else "unavailable",
            redis="ok" if redis_ok else "unavailable",
        ),
    )
    return JSONResponse(
        status_code=200 if body.status == "ready" else 503,
        content=body.model_dump(),
    )
