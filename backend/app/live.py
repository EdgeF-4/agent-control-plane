"""In-process pub/sub for the live dashboard feed, scoped per tenant.

Single-instance by design for phase 1. A multi-instance deployment would back
this with a shared broker; the publish/subscribe surface stays the same.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, tenant_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[tenant_id].add(queue)
        return queue

    def unsubscribe(self, tenant_id: str, queue: asyncio.Queue) -> None:
        self._subs[tenant_id].discard(queue)

    async def publish(self, tenant_id: str, message: dict) -> None:
        for queue in list(self._subs.get(tenant_id, ())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A slow client never blocks the producer; it just drops frames.
                pass
