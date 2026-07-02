"""Live pub/sub for the dashboard feed, scoped per tenant.

Single-instance by default: events fan out in-process to the WebSocket clients
connected to this instance. Point it at a shared broker (Redis) and the same
``publish``/``subscribe`` surface fans out across *every* instance — a run
recorded or a budget alert fired on one instance reaches a dashboard connected
to any other. If the broker is unset or unreachable at startup, the bus falls
back to in-process delivery, so a single-instance install needs no broker at
all and a broker outage degrades to local-only rather than dropping the feed.

Delivery model: with a broker, every frame is published to the broker and
delivered *only* from the broker subscription — including on the instance that
published it. That gives exactly one delivery per connected client with no
per-message de-duplication. Without a broker, ``publish`` delivers straight to
the local subscribers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from typing import Awaitable, Callable, Protocol

_log = logging.getLogger("control_plane")

# An envelope carries the tenant a frame belongs to plus the frame itself, so a
# single shared channel can serve every tenant and the receiver routes locally.
OnEnvelope = Callable[[dict], Awaitable[None]]


class LocalFanout:
    """In-process fanout to the WebSocket subscribers on *this* instance."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, tenant_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[tenant_id].add(queue)
        return queue

    def unsubscribe(self, tenant_id: str, queue: asyncio.Queue) -> None:
        subs = self._subs.get(tenant_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                self._subs.pop(tenant_id, None)

    def deliver(self, tenant_id: str, message: dict) -> None:
        for queue in list(self._subs.get(tenant_id, ())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A slow client never blocks the producer; it just drops frames.
                pass


class Broker(Protocol):
    """A shared message channel every instance publishes to and reads from."""

    async def start(self, on_envelope: OnEnvelope) -> None: ...

    async def publish(self, envelope: dict) -> None: ...

    async def close(self) -> None: ...


class MemoryWire:
    """An in-process stand-in for a broker shared by several :class:`MemoryBroker`s.

    Two brokers registered on the same wire deliver to each other, which is how
    the multi-instance fanout is exercised deterministically in tests without a
    running Redis. It is not for production — a real deployment spans processes.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[OnEnvelope]] = defaultdict(list)

    def register(self, channel: str, handler: OnEnvelope) -> None:
        self._handlers[channel].append(handler)

    def unregister(self, channel: str, handler: OnEnvelope) -> None:
        handlers = self._handlers.get(channel)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def publish(self, channel: str, envelope: dict) -> None:
        for handler in list(self._handlers.get(channel, ())):
            await handler(envelope)


class MemoryBroker:
    """A :class:`Broker` backed by a :class:`MemoryWire` (tests / single box)."""

    def __init__(self, wire: MemoryWire, channel: str) -> None:
        self._wire = wire
        self._channel = channel
        self._handler: OnEnvelope | None = None

    async def start(self, on_envelope: OnEnvelope) -> None:
        self._handler = on_envelope
        self._wire.register(self._channel, on_envelope)

    async def publish(self, envelope: dict) -> None:
        await self._wire.publish(self._channel, envelope)

    async def close(self) -> None:
        if self._handler is not None:
            self._wire.unregister(self._channel, self._handler)
            self._handler = None


class RedisBroker:
    """A :class:`Broker` over Redis pub/sub.

    ``redis`` is imported lazily so it is only required when a deployment
    actually configures a Redis-backed bus; a single-instance install never
    imports it.
    """

    def __init__(self, url: str, channel: str) -> None:
        self._url = url
        self._channel = channel
        self._redis = None
        self._pubsub = None
        self._reader: asyncio.Task | None = None
        self._on_envelope: OnEnvelope | None = None

    async def start(self, on_envelope: OnEnvelope) -> None:
        import redis.asyncio as redis_asyncio  # lazy: optional dependency

        self._on_envelope = on_envelope
        self._redis = redis_asyncio.from_url(self._url)
        # Fail fast if the broker is unreachable so the caller can fall back.
        await self._redis.ping()
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(self._channel)
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._pubsub is not None and self._on_envelope is not None
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    envelope = json.loads(message["data"])
                except (ValueError, TypeError):
                    continue
                await self._on_envelope(envelope)
        except asyncio.CancelledError:
            raise
        except Exception:  # a broker hiccup must not take the process down
            _log.exception("live bus redis reader stopped")

    async def publish(self, envelope: dict) -> None:
        assert self._redis is not None
        await self._redis.publish(self._channel, json.dumps(envelope))

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.unsubscribe(self._channel)
                await self._pubsub.aclose()
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()


class EventBus:
    """The publish/subscribe surface the routers and services use.

    Construct with a broker for multi-instance fanout, or without for a
    single-instance install. ``start`` connects the broker (falling back to
    in-process delivery if it cannot); ``close`` tears it down.
    """

    def __init__(self, broker: Broker | None = None) -> None:
        self._local = LocalFanout()
        self._broker = broker
        self._broker_ready = False

    # -- WebSocket subscribers (always local to this instance) ------------- #
    def subscribe(self, tenant_id: str) -> asyncio.Queue:
        return self._local.subscribe(tenant_id)

    def unsubscribe(self, tenant_id: str, queue: asyncio.Queue) -> None:
        self._local.unsubscribe(tenant_id, queue)

    # -- lifecycle --------------------------------------------------------- #
    async def start(self) -> None:
        if self._broker is None:
            return
        try:
            await self._broker.start(self._on_envelope)
            self._broker_ready = True
            _log.info("live bus: shared broker connected")
        except Exception:
            # Graceful degradation: keep serving this instance's own clients.
            _log.warning(
                "live bus: shared broker unavailable, using in-process delivery",
                exc_info=True,
            )
            self._broker = None
            self._broker_ready = False

    async def close(self) -> None:
        if self._broker is not None and self._broker_ready:
            await self._broker.close()
        self._broker_ready = False

    @property
    def multi_instance(self) -> bool:
        return self._broker_ready

    # -- publish ----------------------------------------------------------- #
    async def _on_envelope(self, envelope: dict) -> None:
        self._local.deliver(envelope.get("tenant", ""), envelope.get("message", {}))

    async def publish(self, tenant_id: str, message: dict) -> None:
        if self._broker is not None and self._broker_ready:
            try:
                await self._broker.publish({"tenant": tenant_id, "message": message})
                return
            except Exception:
                # A transient broker failure still reaches local clients.
                _log.warning("live bus: publish failed, delivering in-process", exc_info=True)
        self._local.deliver(tenant_id, message)


def build_bus(settings) -> EventBus:
    """Build the event bus from settings: Redis-backed if configured, else local."""
    cfg = getattr(settings, "bus", None)
    if cfg is not None and cfg.backend == "redis" and cfg.redis_url:
        channel = f"{cfg.channel_prefix}:events"
        return EventBus(RedisBroker(cfg.redis_url, channel))
    return EventBus(None)
