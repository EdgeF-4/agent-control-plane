"""Anchor routes — WORM Merkle anchoring of the recorder hash-chain.

Read the anchor log, create a new anchor now (admin), and verify both the
anchor log's own chain and that no anchored run has drifted since.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ..anchor import Anchorer
from ..deps import CurrentUser, get_current_user, get_hub, require_admin
from ..engines import EngineHub

router = APIRouter(prefix="/anchors", tags=["anchors"])


def _anchorer(request: Request, hub: EngineHub) -> Anchorer:
    return Anchorer(request.app.state.settings, hub.recorder_config)


@router.get("")
async def list_anchors(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    anchorer = _anchorer(request, hub)
    entries = anchorer.store.entries()
    return {
        "enabled": bool(request.app.state.settings.anchor.enabled),
        "count": len(entries),
        # Newest first, without the (potentially large) per-run list.
        "anchors": [
            {"seq": e["seq"], "ts": e["ts"], "run_count": e["run_count"],
             "merkle_root": e["merkle_root"], "anchor_hash": e["anchor_hash"]}
            for e in reversed(entries)
        ],
    }


@router.post("", status_code=201)
async def create_anchor(
    request: Request,
    current: CurrentUser = Depends(require_admin),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    anchorer = _anchorer(request, hub)
    record = await asyncio.to_thread(anchorer.run_once)
    return {
        "seq": record["seq"], "ts": record["ts"], "run_count": record["run_count"],
        "merkle_root": record["merkle_root"], "anchor_hash": record["anchor_hash"],
        "s3": record.get("s3"),
    }


@router.get("/verify")
async def verify_anchors(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    anchorer = _anchorer(request, hub)
    return await asyncio.to_thread(anchorer.verify)
