"""WORM anchoring of the recorder hash-chain.

The recorder makes each run *tamper-evident*: edit an event and verification
reports where the chain broke. But anyone who can rewrite a run file can also
recompute its chain, so on its own it is not *non-repudiable*. Anchoring closes
that gap: on a cadence, it computes a Merkle root over every run's current head
hash and pins it to an append-only, itself-hash-chained anchor file — and,
optionally, to a write-once S3-compatible bucket. Once a root is anchored
off-box, a later edit to any anchored run no longer matches the pinned root, and
the anchor log's own chain makes back-dating an anchor evident too.

Everything here is standard library: the Merkle tree, the anchor chain, and the
S3 PUT (AWS Signature V4) are all hashlib/hmac, so it stays air-gappable and
adds no dependency. Point it at MinIO, R2, or S3 and it just works.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import flight_recorder as fr
from flight_recorder.hashchain import GENESIS_HASH
from flight_recorder.schema import canonical_json


# --------------------------------------------------------------------------- #
# Merkle root
# --------------------------------------------------------------------------- #
def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def merkle_root(leaf_hashes: list[str]) -> str:
    """A binary SHA-256 Merkle root over ordered leaf hashes.

    An empty set roots to the genesis hash. An odd node at any level is paired
    with itself (the standard duplicate-last rule).
    """
    if not leaf_hashes:
        return GENESIS_HASH
    level = list(leaf_hashes)
    while len(level) > 1:
        nxt: list[str] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            nxt.append(_sha256_hex(left + right))
        level = nxt
    return level[0]


def _run_leaf(run: dict) -> str:
    return _sha256_hex(f"{run['run_id']}\n{run['head_hash']}\n{run['event_count']}")


# --------------------------------------------------------------------------- #
# Building an anchor over the recorder's runs
# --------------------------------------------------------------------------- #
def _run_ids(recorder_config: dict) -> list[str]:
    log_dir = recorder_config.get("log_dir", ".")
    try:
        names = [n for n in os.listdir(log_dir) if n.endswith(".jsonl")]
    except FileNotFoundError:
        return []
    return sorted(n[: -len(".jsonl")] for n in names)


def _run_head(recorder_config: dict, run_id: str) -> tuple[str, int, bool]:
    """Return ``(head_hash, event_count, chain_ok)`` for a run, or genesis/empty."""
    try:
        events = fr.load_events(run_id, config=recorder_config)
    except FileNotFoundError:
        return GENESIS_HASH, 0, False
    if not events:
        return GENESIS_HASH, 0, True
    head = events[-1].get("hash", GENESIS_HASH)
    return head, len(events), fr.verify_chain(events).ok


def build_anchor_record(recorder_config: dict, *, now_iso: str | None = None) -> dict:
    """Snapshot every run's head hash and compute the Merkle root over them."""
    runs = []
    for run_id in _run_ids(recorder_config):
        head, count, _ok = _run_head(recorder_config, run_id)
        runs.append({"run_id": run_id, "head_hash": head, "event_count": count})
    runs.sort(key=lambda r: r["run_id"])
    root = merkle_root([_run_leaf(r) for r in runs])
    return {
        "ts": now_iso or datetime.now(timezone.utc).isoformat(),
        "run_count": len(runs),
        "runs": runs,
        "merkle_root": root,
    }


def _anchor_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "anchor_hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# The append-only, hash-chained anchor log
# --------------------------------------------------------------------------- #
class AnchorStore:
    """An append-only NDJSON anchor log. Each record chains to the previous one
    (``prev_anchor_hash``), so the log itself is tamper-evident."""

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def entries(self) -> list[dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except FileNotFoundError:
            return []

    def latest(self) -> dict | None:
        entries = self.entries()
        return entries[-1] if entries else None

    def append(self, record: dict) -> dict:
        entries = self.entries()
        prev = entries[-1]["anchor_hash"] if entries else GENESIS_HASH
        sealed = dict(record)
        sealed["seq"] = len(entries)
        sealed["prev_anchor_hash"] = prev
        sealed["anchor_hash"] = _anchor_hash(sealed)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(sealed, sort_keys=True) + "\n")
        return sealed


def verify_anchor_log(store: AnchorStore) -> dict:
    """Verify the anchor log's own chain and each record's internal Merkle root."""
    entries = store.entries()
    expected_prev = GENESIS_HASH
    for i, e in enumerate(entries):
        if e.get("seq") != i:
            return {"ok": False, "count": len(entries), "broken_seq": i,
                    "reason": f"anchor seq out of order at index {i}"}
        if e.get("prev_anchor_hash") != expected_prev:
            return {"ok": False, "count": len(entries), "broken_seq": e.get("seq"),
                    "reason": "anchor prev_hash does not link to the previous anchor"}
        if _anchor_hash(e) != e.get("anchor_hash"):
            return {"ok": False, "count": len(entries), "broken_seq": e.get("seq"),
                    "reason": "anchor record was modified after it was written"}
        recomputed = merkle_root([_run_leaf(r) for r in e.get("runs", [])])
        if recomputed != e.get("merkle_root"):
            return {"ok": False, "count": len(entries), "broken_seq": e.get("seq"),
                    "reason": "merkle root does not match the anchored run set"}
        expected_prev = e["anchor_hash"]
    return {"ok": True, "count": len(entries), "broken_seq": None, "reason": None}


def verify_runs_against_anchor(recorder_config: dict, anchor: dict) -> list[dict]:
    """Runs whose current head no longer matches what an anchor pinned (drift)."""
    drift = []
    for r in anchor.get("runs", []):
        head, _count, chain_ok = _run_head(recorder_config, r["run_id"])
        if head != r["head_hash"] or not chain_ok:
            drift.append({
                "run_id": r["run_id"],
                "anchored_head": r["head_hash"],
                "current_head": head,
                "chain_ok": chain_ok,
            })
    return drift


# --------------------------------------------------------------------------- #
# Optional S3-compatible upload (AWS Signature V4, standard library)
# --------------------------------------------------------------------------- #
def _sigv4_key(secret: str, date: str, region: str, service: str) -> bytes:
    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(("AWS4" + secret).encode("utf-8"), date)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def sign_s3_put(
    *, endpoint_url: str, region: str, bucket: str, key: str, body: bytes,
    access_key: str, secret_key: str, now: datetime | None = None,
    service: str = "s3",
) -> tuple[str, dict]:
    """Compute the request URL and signed headers for a path-style S3 PUT.

    Returns ``(url, headers)``; kept pure (no I/O) so the signing is testable.
    """
    now = now or datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")

    base = endpoint_url.rstrip("/")
    host = base.split("://", 1)[-1].split("/", 1)[0]
    canonical_uri = f"/{bucket}/{key}"
    payload_hash = hashlib.sha256(body).hexdigest()

    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join(
        ["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        _sigv4_key(secret_key, date, region, service),
        string_to_sign.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    out_headers = {
        "Authorization": authorization,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "content-type": "application/json",
    }
    return f"{base}{canonical_uri}", out_headers


def s3_put(cfg, key: str, body: bytes, *, now: datetime | None = None) -> dict:
    """Upload one object to the configured S3-compatible endpoint via SigV4."""
    access_key = os.environ.get(cfg.s3_access_key_env or "", "")
    secret_key = os.environ.get(cfg.s3_secret_key_env or "", "")
    if not (cfg.s3_endpoint_url and cfg.s3_bucket and access_key and secret_key):
        return {"ok": False, "detail": "s3 anchoring not fully configured"}
    url, headers = sign_s3_put(
        endpoint_url=cfg.s3_endpoint_url, region=cfg.s3_region, bucket=cfg.s3_bucket,
        key=key, body=body, access_key=access_key, secret_key=secret_key, now=now,
    )
    request = urllib.request.Request(url, data=body, method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return {"ok": True, "status": response.status, "key": key}
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        return {"ok": False, "status": exc.code,
                "detail": exc.read().decode("utf-8", "replace")[:300]}
    except urllib.error.URLError as exc:  # pragma: no cover - network dependent
        return {"ok": False, "detail": str(exc.reason)}


# --------------------------------------------------------------------------- #
# The anchorer: build + append + optional upload, and verify
# --------------------------------------------------------------------------- #
class Anchorer:
    def __init__(self, settings, recorder_config: dict) -> None:
        self.cfg = settings.anchor
        self.recorder_config = recorder_config
        self.store = AnchorStore(settings.engine_path("anchor", "anchors.ndjson"))

    def run_once(self, *, now_iso: str | None = None) -> dict:
        record = build_anchor_record(self.recorder_config, now_iso=now_iso)
        sealed = self.store.append(record)
        upload = None
        if self.cfg.s3_endpoint_url:
            key = f"{self.cfg.s3_prefix}/anchor-{sealed['seq']:08d}.json"
            upload = s3_put(self.cfg, key, (json.dumps(sealed, sort_keys=True)).encode("utf-8"))
        return {**sealed, "s3": upload}

    def verify(self) -> dict:
        log_result = verify_anchor_log(self.store)
        latest = self.store.latest()
        drift = verify_runs_against_anchor(self.recorder_config, latest) if latest else []
        return {
            "ok": bool(log_result["ok"]) and not drift,
            "anchor_log": log_result,
            "anchor_count": len(self.store.entries()),
            "latest_merkle_root": latest["merkle_root"] if latest else None,
            "drift": drift,
        }
