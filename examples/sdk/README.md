# Quickstart SDK

A single-file, dependency-free Python client for reporting agent runs to the
control plane. Copy [`control_plane_client.py`](control_plane_client.py) into
your project. It uses only the standard library.

## Authenticate

Runs are ingested with a **per-project API key**, not an operator login. Mint one
in the dashboard under **Admin → Projects → Keys** (or `POST /api/v1/projects/<slug>/keys`).
The plaintext key is shown once; only its hash is stored.

## Report a run

```python
from control_plane_client import ControlPlane

cp = ControlPlane("https://control-plane.example", api_key="acp_...")

with cp.open_run(agent_name="nightly-report", label="weekly digest") as run:
    outcome = run.report_usage(
        model="assistant-large", input_tokens=1200, output_tokens=350, cost_usd=0.12,
    )
    if not outcome["allowed"]:
        raise SystemExit(
            "budget kill switch engaged; ask an administrator to release or "
            "raise the project budget before retrying"
        )

    run.record_tool_call(tool="search", server="web", arguments={"q": "market size"})
# the run closes automatically on exit (as "error" if the block raised)
```

Every call is priced against the project budget, checked against policy, and
sealed into the tamper-evident audit chain. If the budget cap trips, the run is
killed and further work is denied until an operator releases it.

## Run the example

```bash
export ACP_URL=http://127.0.0.1:8800
export ACP_API_KEY=acp_your_project_key
python quickstart.py
```

## Same thing with curl

```bash
BASE=http://127.0.0.1:8800; KEY=acp_your_project_key
RUN=$(curl --fail-with-body --show-error -sS -X POST $BASE/api/v1/runs -H "authorization: Bearer $KEY" \
  -H 'content-type: application/json' -d '{"agent_name":"curl","label":"demo"}' | jq -r .id)
curl --fail-with-body --show-error -sS -X POST $BASE/api/v1/runs/$RUN/usage -H "authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"model":"assistant-large","input_tokens":1200,"output_tokens":350,"cost_usd":0.12}'
curl --fail-with-body --show-error -sS -X POST $BASE/api/v1/runs/$RUN/complete -H "authorization: Bearer $KEY" \
  -H 'content-type: application/json' -d '{"status":"completed"}'
```

On an HTTP failure, the response contains `detail` and `next_action`. Follow
`next_action`, then rerun the failed command. If curl cannot connect, confirm
the stack is running and `BASE` is correct before retrying.
