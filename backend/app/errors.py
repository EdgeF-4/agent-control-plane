"""Consistent API error responses with a concrete caller recovery step."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_log = logging.getLogger("control_plane.errors")


def next_action_for_status(status_code: int) -> str:
    """Return an operator-safe recovery instruction for an HTTP status."""
    if status_code == 400:
        return "Correct the request described in detail, then retry."
    if status_code == 401:
        return (
            "Sign in again or replace the invalid or revoked credential, then retry "
            "with the new credential."
        )
    if status_code == 403:
        return (
            "Use an identity with the required role and project scope, or ask an "
            "administrator to grant access, then retry."
        )
    if status_code == 404:
        return (
            "List the available resources, correct the identifier or path, then retry."
        )
    if status_code == 409:
        return (
            "Refresh the resource, resolve the state conflict described in detail, "
            "then retry."
        )
    if status_code == 422:
        return "Correct the fields listed in detail, then retry the request."
    if status_code == 429:
        return "Wait for the limit window to reset, then retry."
    if status_code >= 500:
        return (
            "Retry once. If it fails again, run 'docker compose logs backend' and "
            "give the operator the request path and time."
        )
    return "Correct the condition described in detail, then retry."


def error_payload(detail: Any, status_code: int) -> dict[str, Any]:
    """Build the stable error envelope used by all HTTP failures."""
    return {
        "detail": jsonable_encoder(detail),
        "next_action": next_action_for_status(status_code),
    }


def install_error_handlers(app: FastAPI) -> None:
    """Make framework, validation, and unexpected failures actionable."""

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=422,
            content=error_payload(error.errors(), 422),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content=error_payload(error.detail, error.status_code),
            headers=error.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        _log.exception(
            "request failed unexpectedly; inspect this traceback, correct the root "
            "cause, and retry the request",
            exc_info=error,
        )
        return JSONResponse(
            status_code=500,
            content=error_payload("Internal server error.", 500),
        )
