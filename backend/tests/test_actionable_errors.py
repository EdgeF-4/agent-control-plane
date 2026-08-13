"""Every API failure family gives the caller a concrete recovery action."""

from __future__ import annotations

import asyncio
import json

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.errors import install_error_handlers
from app.config import load_settings


def _error_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    class Payload(BaseModel):
        count: int

    @app.get("/missing-project")
    async def missing_project() -> None:
        raise HTTPException(404, "project not found")

    @app.get("/expired-token")
    async def expired_token() -> None:
        raise HTTPException(401, "invalid or expired token")

    @app.get("/conflict")
    async def conflict() -> None:
        raise HTTPException(409, "run is completed")

    @app.post("/validation")
    async def validation(payload: Payload) -> dict:
        return payload.model_dump()

    @app.get("/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("simulated dependency failure")

    return app


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=_error_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        return await client.request(method, path, **kwargs)


def _assert_actionable(response, expected_status: int, required_word: str) -> None:
    assert response.status_code == expected_status
    body = response.json()
    assert body["detail"]
    assert body["next_action"]
    assert required_word in body["next_action"].lower()


def test_not_found_says_how_to_recover() -> None:
    response = asyncio.run(_request("GET", "/missing-project"))
    _assert_actionable(response, 404, "list")


def test_auth_failure_says_how_to_recover() -> None:
    response = asyncio.run(_request("GET", "/expired-token"))
    _assert_actionable(response, 401, "sign in")


def test_conflict_says_how_to_recover() -> None:
    response = asyncio.run(_request("GET", "/conflict"))
    _assert_actionable(response, 409, "refresh")


def test_validation_failure_says_how_to_recover() -> None:
    response = asyncio.run(_request("POST", "/validation", json={}))
    _assert_actionable(response, 422, "correct")


def test_unexpected_failure_says_how_to_recover() -> None:
    response = asyncio.run(_request("GET", "/unexpected"))
    _assert_actionable(response, 500, "logs")


def test_invalid_config_says_how_to_recover(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{broken", encoding="utf-8")
    try:
        load_settings(str(path))
    except ValueError as error:
        message = str(error)
        assert "python -m json.tool config.json" in message
        assert "correct" in message.lower()
    else:
        raise AssertionError("invalid JSON should fail")


def test_schema_error_says_how_to_recover(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"auth": {}}), encoding="utf-8")
    try:
        load_settings(str(path))
    except ValueError as error:
        message = str(error)
        assert "config.example.json" in message
        assert "correct" in message.lower()
    else:
        raise AssertionError("invalid config schema should fail")
