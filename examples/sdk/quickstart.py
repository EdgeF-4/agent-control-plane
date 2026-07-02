"""Quickstart: report one agent run to the control plane.

    # 1. In the dashboard: Admin -> Projects -> Keys -> mint a key for a project.
    # 2. Point this at your control plane and run it:
    export ACP_URL=http://127.0.0.1:8800
    export ACP_API_KEY=acp_your_project_key
    python quickstart.py

Then open the dashboard: the run, its cost against the project budget, the tool
decision, and the tamper-evident timeline are all there.
"""

from __future__ import annotations

import os
import sys

from control_plane_client import ControlPlane, ControlPlaneError


def main() -> int:
    base_url = os.environ.get("ACP_URL", "http://127.0.0.1:8800")
    api_key = os.environ.get("ACP_API_KEY")
    if not api_key:
        print("set ACP_API_KEY to a project ingest key (Admin -> Projects -> Keys)", file=sys.stderr)
        return 2

    cp = ControlPlane(base_url, api_key)
    try:
        with cp.open_run(agent_name="quickstart", label="hello control plane") as run:
            # Report a model call — priced and checked against the project budget.
            outcome = run.report_usage(
                model="assistant-large", input_tokens=1200, output_tokens=350, cost_usd=0.12,
            )
            print(f"usage recorded: ${outcome['cost_usd']:.4f}, budget state {outcome['state']}")
            if not outcome["allowed"]:
                print("budget kill switch engaged — stopping early")
                return 0

            # Record a tool call and see the policy decision.
            decision = run.record_tool_call(tool="search", server="web", arguments={"q": "market size 2026"})
            print(f"tool call 'web/search' -> {decision['decision']} ({decision['reason']})")

        print(f"run {run.id} closed as {run.status}. Open the dashboard to see it end to end.")
        return 0
    except ControlPlaneError as exc:
        print(f"control plane refused the call: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
