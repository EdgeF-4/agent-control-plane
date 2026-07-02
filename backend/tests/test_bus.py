"""Live event bus: in-process fallback, cross-instance fanout, graceful degrade.

The cross-instance behaviour is proven deterministically with an in-memory wire
shared by two bus instances (no Redis needed). An opt-in integration test runs
the same assertions over a real Redis when ``ACP_TEST_REDIS_URL`` is reachable.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.live import EventBus, MemoryBroker, MemoryWire, RedisBroker, build_bus
from app.config import Settings


async def _drain(queue: asyncio.Queue, timeout: float = 1.0) -> dict:
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def test_single_instance_delivery_without_broker():
    bus = EventBus()
    await bus.start()  # no broker: a no-op
    assert bus.multi_instance is False
    q = bus.subscribe("tenant-a")
    await bus.publish("tenant-a", {"type": "run.created", "n": 1})
    assert (await _drain(q))["n"] == 1
    await bus.close()


async def test_tenant_scoping_is_enforced():
    bus = EventBus()
    qa = bus.subscribe("tenant-a")
    qb = bus.subscribe("tenant-b")
    await bus.publish("tenant-a", {"hi": "a"})
    assert (await _drain(qa))["hi"] == "a"
    assert qb.empty()  # b never sees a's frame
    await bus.close()


async def test_cross_instance_fanout_over_shared_wire():
    # Two bus instances on one wire model two gateway processes behind Redis.
    wire = MemoryWire()
    bus_a = EventBus(MemoryBroker(wire, "acp:events"))
    bus_b = EventBus(MemoryBroker(wire, "acp:events"))
    await bus_a.start()
    await bus_b.start()
    assert bus_a.multi_instance and bus_b.multi_instance

    # A dashboard connected to instance B; the run is recorded on instance A.
    qb = bus_b.subscribe("acme")
    qa = bus_a.subscribe("acme")
    await bus_a.publish("acme", {"type": "budget.alert", "kind": "cap"})

    got_b = await _drain(qb)
    got_a = await _drain(qa)
    assert got_b["kind"] == "cap"  # reached the other instance
    assert got_a["kind"] == "cap"  # and its own, exactly once
    assert qa.empty() and qb.empty()  # no duplicate delivery

    await bus_a.close()
    await bus_b.close()


class _DeadBroker:
    async def start(self, on_envelope):
        raise ConnectionError("broker down")

    async def publish(self, envelope):  # pragma: no cover - never reached
        raise AssertionError("should not publish to a dead broker")

    async def close(self):  # pragma: no cover
        pass


async def test_broker_unavailable_falls_back_to_local():
    bus = EventBus(_DeadBroker())
    await bus.start()  # start fails internally, degrades to local
    assert bus.multi_instance is False
    q = bus.subscribe("acme")
    await bus.publish("acme", {"ok": True})
    assert (await _drain(q))["ok"] is True
    await bus.close()


def test_build_bus_selects_backend(tmp_path):
    local = build_bus(Settings(auth={"jwt_secret": "x"}))
    assert local.multi_instance is False  # default memory backend, no broker

    redis_cfg = Settings(
        auth={"jwt_secret": "x"},
        bus={"backend": "redis", "redis_url": "redis://localhost:6379/0"},
    )
    bus = build_bus(redis_cfg)
    assert isinstance(bus._broker, RedisBroker)


# --- opt-in real-Redis integration ---------------------------------------- #
_REDIS_URL = os.environ.get("ACP_TEST_REDIS_URL")


async def _redis_reachable(url: str) -> bool:
    try:
        import redis.asyncio as redis_asyncio
    except Exception:
        return False
    try:
        client = redis_asyncio.from_url(url)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _REDIS_URL, reason="set ACP_TEST_REDIS_URL to run")
async def test_cross_instance_fanout_over_real_redis():
    if not await _redis_reachable(_REDIS_URL):
        pytest.skip("ACP_TEST_REDIS_URL not reachable")
    channel = "acp:test:events"
    bus_a = EventBus(RedisBroker(_REDIS_URL, channel))
    bus_b = EventBus(RedisBroker(_REDIS_URL, channel))
    await bus_a.start()
    await bus_b.start()
    try:
        qb = bus_b.subscribe("acme")
        await asyncio.sleep(0.1)  # let the subscription settle
        await bus_a.publish("acme", {"type": "run.created", "via": "redis"})
        assert (await _drain(qb, timeout=3.0))["via"] == "redis"
    finally:
        await bus_a.close()
        await bus_b.close()
