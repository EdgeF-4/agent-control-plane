"""The copied SDK and command entrypoint keep raw causes and recovery steps."""

from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SDK_DIR = ROOT / "examples" / "sdk"
sys.path.insert(0, str(SDK_DIR))

from control_plane_client import ControlPlane, ControlPlaneError  # noqa: E402


def test_sdk_http_error_uses_server_detail_and_next_action() -> None:
    body = json.dumps(
        {
            "detail": "invalid or expired token",
            "next_action": "Mint a new project key, replace ACP_API_KEY, then retry.",
        }
    ).encode()
    failure = urllib.error.HTTPError(
        "http://example.invalid/api/v1/runs", 401, "Unauthorized", {}, BytesIO(body)
    )
    client = ControlPlane("http://example.invalid", "invalid-key")
    with patch("urllib.request.urlopen", side_effect=failure):
        try:
            client._post("/api/v1/runs", {})
        except ControlPlaneError as error:
            assert error.status == 401
            assert "invalid or expired token" in str(error)
            assert "Mint a new project key" in str(error)
        else:
            raise AssertionError("HTTP refusal should raise ControlPlaneError")


def test_sdk_network_error_names_url_and_recovery() -> None:
    client = ControlPlane("http://example.invalid", "key")
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        try:
            client._post("/api/v1/runs", {})
        except ControlPlaneError as error:
            assert "connection refused" in str(error)
            assert "confirm the stack is running and ACP_URL is correct" in str(error)
        else:
            raise AssertionError("network refusal should raise ControlPlaneError")


def test_sdk_nonserializable_request_names_correction() -> None:
    client = ControlPlane("http://example.invalid", "key")
    try:
        client._post("/api/v1/runs", {"bad": object()})
    except ControlPlaneError as error:
        assert "not JSON serializable" in str(error)
        assert "make every request field JSON-serializable" in str(error)
    else:
        raise AssertionError("nonserializable request should fail")


def test_cli_bad_command_names_help_and_retry() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "backend")
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "not-a-command"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert "run 'control-plane --help'" in result.stderr
    assert "then retry" in result.stderr


def test_quickstart_missing_key_names_export_and_rerun() -> None:
    env = os.environ.copy()
    env.pop("ACP_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "quickstart.py"],
        cwd=SDK_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "export ACP_API_KEY=<project-key>" in result.stderr
    assert "python quickstart.py" in result.stderr
