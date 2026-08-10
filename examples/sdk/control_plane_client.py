"""A tiny, dependency-free client for the Agent Control Plane.

Standard library only. Copy this one file into your agent and you can open a
run, report what it spent, record the tool calls it made, and close it out. It
authenticates with a per-project ingest key (create one in the dashboard under
Admin -> Projects -> Keys), so an agent never needs an operator's login.

    from control_plane_client import ControlPlane

    cp = ControlPlane("https://control-plane.example", api_key="acp_...")
    with cp.open_run(agent_name="nightly-report", label="weekly digest") as run:
        run.report_usage(model="assistant-large", input_tokens=1200, output_tokens=350, cost_usd=0.12)
        run.record_tool_call(tool="search", server="web", arguments={"q": "market size"})
    # the run closes automatically; open the dashboard to see it end to end
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional


class ControlPlaneError(Exception):
    """An API call was refused (bad key, budget kill switch, gate, etc.)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class ControlPlane:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                # Bearer and X-API-Key are both accepted; Bearer keeps it simple.
                "authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise ControlPlaneError(exc.code, detail) from exc

    def open_run(self, *, agent_name: str = "", label: str = "",
                 meta: Optional[dict] = None) -> "Run":
        """Open a run. The API key already implies the project."""
        data = self._post("/api/v1/runs", {
            "agent_name": agent_name, "label": label, "meta": meta or {},
        })
        return Run(self, data["id"])


class Run:
    def __init__(self, client: ControlPlane, run_id: str) -> None:
        self._client = client
        self.id = run_id
        self.status = "running"

    def report_usage(self, *, model: str, input_tokens: int = 0, output_tokens: int = 0,
                     cost_usd: Optional[float] = None, provider: str = "local",
                     name: Optional[str] = None, latency_ms: Optional[float] = None) -> dict:
        """Report one model call's usage. The control plane prices it (or takes
        your ``cost_usd``), enforces the budget, and seals it into the audit log.
        The returned outcome tells you if the budget kill switch just tripped."""
        outcome = self._client._post(f"/api/v1/runs/{self.id}/usage", {
            "provider": provider, "model": model,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_usd": cost_usd, "name": name, "latency_ms": latency_ms,
        })
        self.status = outcome.get("run_status", self.status)
        return outcome

    def record_tool_call(self, *, tool: str, server: str = "tools",
                         arguments: Optional[dict] = None) -> dict:
        """Record a tool call and get the allow/deny policy decision back."""
        return self._client._post(f"/api/v1/runs/{self.id}/tool-call", {
            "server": server, "tool": tool, "arguments": arguments or {},
        })

    def close(self, *, status: str = "completed", output: Optional[dict] = None) -> dict:
        """Close the run (``completed`` or ``error``) and seal the final event."""
        result = self._client._post(f"/api/v1/runs/{self.id}/complete", {
            "status": status, "output": output,
        })
        self.status = result.get("status", status)
        return result

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Close as an error if the block raised, and only if not already closed
        # (a budget kill switch may have closed it mid-run).
        if self.status == "running":
            self.close(status="error" if exc_type else "completed")
