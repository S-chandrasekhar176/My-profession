"""Unit tests for EventBus priority dispatching and subscriptions."""
import asyncio
import pytest
from core.event_bus import EventBus, EventPriority


@pytest.mark.asyncio
class TestEventBus:
    async def test_priority_scheduling_order(self):
        """Events published with HIGH priority must be dispatched before LOW priority."""
        bus = EventBus()
        bus.start()

        execution_order = []

        async def handler(event_name, payload):
            execution_order.append(payload)

        bus.subscribe("test_event", handler)

        # Enqueue in reverse priority order: LOW, then MEDIUM, then HIGH
        await bus.publish("test_event", payload="LOW_1", priority=EventPriority.LOW)
        await bus.publish("test_event", payload="LOW_2", priority=EventPriority.LOW)
        await bus.publish("test_event", payload="HIGH_1", priority=EventPriority.HIGH)
        await bus.publish("test_event", payload="MED_1", priority=EventPriority.MEDIUM)

        # Allow worker loop to process
        await asyncio.sleep(0.05)
        await bus.stop()

        # HIGH must run first, then MEDIUM, then LOW
        assert execution_order[0] == "HIGH_1"
        assert execution_order[1] == "MED_1"
        assert "LOW_1" in execution_order[2:]
        assert "LOW_2" in execution_order[2:]

    async def test_wildcard_subscription(self):
        """Subscriber to '*' should receive all published events."""
        bus = EventBus()
        bus.start()

        received = []

        async def wildcard_handler(event_name, payload):
            received.append((event_name, payload))

        bus.subscribe("*", wildcard_handler)

        await bus.publish("tick.reliance", payload=2500.0, priority=EventPriority.HIGH)
        await bus.publish("bar.1m.sbin", payload={"close": 800.0}, priority=EventPriority.MEDIUM)

        await asyncio.sleep(0.05)
        await bus.stop()

        assert len(received) == 2
        assert received[0] == ("tick.reliance", 2500.0)
        assert received[1][0] == "bar.1m.sbin"

    async def test_unsubscribe(self):
        """Unsubscribed callback should not receive subsequent events."""
        bus = EventBus()
        bus.start()

        calls = []

        async def handler(event_name, payload):
            calls.append(payload)

        bus.subscribe("ping", handler)
        await bus.publish("ping", 1)
        await asyncio.sleep(0.02)

        bus.unsubscribe("ping", handler)
        await bus.publish("ping", 2)
        await asyncio.sleep(0.02)

        await bus.stop()
        assert calls == [1]

    async def test_publish_nowait(self):
        """Thread-safe publish_nowait should successfully queue events."""
        bus = EventBus()
        bus.start()

        received = []

        async def handler(name, payload):
            received.append(payload)

        bus.subscribe("async_tick", handler)
        ok = bus.publish_nowait("async_tick", "TICK_123", priority=EventPriority.HIGH)
        assert ok is True

        await asyncio.sleep(0.05)
        await bus.stop()
        assert received == ["TICK_123"]
