"""WebSocket live feed.

Browsers cannot set an Authorization header on a WebSocket, so the access token
is passed as a query parameter and verified before the socket is accepted.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from .. import models
from ..security import verify_access_token

router = APIRouter()

_AUTH_FAILURE = (
    "Authentication failed. Sign in again, replace the token in the WebSocket URL, "
    "and reconnect."
)


@router.websocket("/live")
async def live(websocket: WebSocket, token: str = Query(...)) -> None:
    settings = websocket.app.state.settings
    bus = websocket.app.state.bus
    db = websocket.app.state.db

    identity = verify_access_token(settings.auth, token)
    if identity is None:
        await websocket.close(code=4401, reason=_AUTH_FAILURE)
        return
    try:
        user_id = uuid.UUID(identity.client_id)
    except ValueError:
        await websocket.close(code=4401, reason=_AUTH_FAILURE)
        return

    async with db.sessionmaker() as session:
        user = await session.get(models.User, user_id)
        if user is None:
            await websocket.close(code=4401, reason=_AUTH_FAILURE)
            return
        tenant_id = str(user.tenant_id)

    await websocket.accept()
    queue = bus.subscribe(tenant_id)
    await websocket.send_json({"type": "connected", "tenant_id": tenant_id})
    try:
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        raise
    finally:
        bus.unsubscribe(tenant_id, queue)
