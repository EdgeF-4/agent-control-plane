"""Focused unit tests for security and engine composition."""

from __future__ import annotations

import secrets

from app.config import AuthConfig, Settings
from app.engines import EngineHub
from app.security import (
    hash_password,
    mint_access_token,
    verify_access_token,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert verify_password("s3cret-pw", h)
    assert not verify_password("wrong", h)


def test_token_roundtrip_and_rejection():
    auth = AuthConfig(jwt_secret=secrets.token_hex(32))
    token = mint_access_token(auth, subject="user-123", roles=["admin"])
    identity = verify_access_token(auth, token)
    assert identity is not None
    assert identity.client_id == "user-123"

    other = AuthConfig(jwt_secret=secrets.token_hex(32))
    assert verify_access_token(other, token) is None  # wrong signing key


def _hub(tmp_path) -> EngineHub:
    settings = Settings(
        auth={"jwt_secret": secrets.token_hex(16)},
        data_dir=str(tmp_path / "data"),
        engines={"policy": {"default": "allow",
                            "rules": [{"client_id": "*", "roles": [], "allow": [],
                                       "deny": ["*__delete*"]}]}},
    )
    return EngineHub(settings)


def test_cost_scopes_are_isolated(tmp_path):
    hub = _hub(tmp_path)
    from cost_governor import usd_to_micro

    for scope in ("t1/alpha", "t2/alpha"):
        hub.ensure_budget(budget_id=scope, name=scope, scope_id=scope,
                          limit_micro=usd_to_micro(10), period="monthly",
                          warn_threshold=0.8, latch=True)
    # Spend $9 only on t1/alpha.
    hub.record_and_check(client="t1", scope_id="t1/alpha", agent="r",
                         provider="local", model="m", input_tokens=0, output_tokens=0,
                         cost_micro=usd_to_micro(9))
    t1 = next(b for b in hub.budget_status("t1/") if b["scope_id"] == "t1/alpha")
    t2 = next(b for b in hub.budget_status("t2/") if b["scope_id"] == "t2/alpha")
    assert t1["spent_micro"] == usd_to_micro(9)
    assert t2["spent_micro"] == 0  # the other tenant's ledger is untouched
    hub.close()


def test_policy_allows_and_denies(tmp_path):
    hub = _hub(tmp_path)
    assert hub.policy_allows("client", ["member"], "web", "search") is True
    assert hub.policy_allows("client", ["member"], "db", "delete_records") is False
    hub.close()


def test_recorder_chain_detects_tampering(tmp_path):
    hub = _hub(tmp_path)
    rid = "t1__alpha__abc123"
    hub.open_run(rid, input={"x": 1})
    hub.emit(rid, "model_response", {"text": "hi"}, tokens={"prompt": 1, "completion": 1}, cost_usd=0.01)
    hub.end_run(rid, status="ok")
    assert hub.verify_run(rid)["ok"] is True

    # Rewrite a line in the on-disk record.
    import json
    from pathlib import Path

    path = Path(hub._recorder_config["log_dir"], f"{rid}.jsonl")
    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["payload"] = {"text": "tampered"}
    lines[1] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    assert hub.verify_run(rid)["ok"] is False
    hub.close()


def test_recorder_resumes_chain_across_restart(tmp_path):
    # Open a run and record into it, then drop all in-process state — the same
    # thing a backend restart does to an in-flight run.
    hub = _hub(tmp_path)
    rid = "t1__alpha__resume01"
    hub.open_run(rid, input={"x": 1})
    hub.emit(rid, "model_response", {"text": "before restart"},
             tokens={"prompt": 1, "completion": 1}, cost_usd=0.01)
    before = hub.verify_run(rid)
    assert before["ok"] is True
    hub.close()

    # A fresh hub over the same data dir has no live RunLog for this run.
    hub2 = _hub(tmp_path)
    assert rid not in hub2._runs
    # Continuing the run rehydrates the cursor and keeps one unbroken chain.
    hub2.emit(rid, "tool_call", {"name": "search"}, name="search")
    hub2.end_run(rid, status="ok")

    after = hub2.verify_run(rid)
    assert after["ok"] is True                             # chain intact
    assert after["event_count"] == before["event_count"] + 2  # tool_call + run_end
    hub2.close()
