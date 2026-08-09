"""Typed error envelope.

Every non-2xx response uses one shape. Messages are safe for clients: no
stack traces, no upstream bodies, no secrets, no SQL.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from drake_api.correlation import CORRELATION_HEADER, correlation_id_var


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str
    retryable: bool = False
    details: list[dict[str, Any]] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    410: "gone",
    412: "precondition_failed",
    422: "validation_error",
    428: "precondition_required",
    429: "rate_limited",
    502: "provider_contract",
    503: "dependency_unavailable",
}


def _envelope(
    status_code: int,
    message: str,
    details: list[dict[str, Any]] | None = None,
    retryable: bool | None = None,
    code: str | None = None,
) -> JSONResponse:
    correlation_id = correlation_id_var.get() or ""
    body = ErrorEnvelope(
        error=ErrorBody(
            code=code or _STATUS_CODES.get(status_code, "error"),
            message=message,
            correlation_id=correlation_id,
            retryable=retryable if retryable is not None else status_code in (429, 503),
            details=details,
        )
    )
    response = JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))
    if correlation_id:
        response.headers[CORRELATION_HEADER] = correlation_id
    return response


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # A raiser may supply a BOUNDED domain code alongside the message:
        # `detail={"code": "plan_stale", "message": "..."}`. Without this it
        # was stringified into the message field, so a client that wanted to
        # tell `plan_stale` from `version_conflict` had to parse a repr of a
        # Python dict — which meant it did not, and every 409 looked alike.
        #
        # Only these two keys are promoted, and the code still has to be a
        # string, so a detail dict can never invent an envelope field.
        detail = exc.detail
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            extra = {k: v for k, v in detail.items() if k not in ("code", "message")}
            return _envelope(
                exc.status_code,
                str(detail.get("message") or ""),
                code=str(detail["code"]),
                details=[extra] if extra else None,
            )
        return _envelope(exc.status_code, str(detail))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {"loc": [str(part) for part in err.get("loc", [])], "type": err.get("type", "invalid")}
            for err in exc.errors()
        ]
        return _envelope(422, "request validation failed", details=details)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, _exc: Exception) -> JSONResponse:
        # Never leak internal exception content to clients.
        return _envelope(500, "internal error")
