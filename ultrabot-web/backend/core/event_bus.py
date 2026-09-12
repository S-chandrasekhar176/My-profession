"""Priority-Driven Event Bus for UltraBot (Phase 1).

Enables decoupled, asynchronous event dispatching with strict priority scheduling:
- HIGH (1): Real-time tick triggers, stop-loss / trailing-stop breaches, hard risk stops.
- MEDIUM (2): Bar closure events, strategy calculation passes, scan signals.
- LOW (3): UI telemetry, database async state syncing, health pings.

Ensures critical stop-loss decisions are never delayed by long-running strategy passes.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from enum import IntEnum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class EventPriority(IntEnum):
    """Event priority tiers. Lower integer = higher scheduling priority."""
    HIGH = 1     # Critical: Tick SL/TP breaches, emergency halts
    MEDIUM = 2   # Normal: Completed candle events, strategy evaluations
    LOW = 3      # Background: Telemetry, UI sync, heartbeat logs


class EventBus:
    """Non-blocking, thread-safe asynchronous priority event bus."""

    def __init__(self, maxsize: int = 10000):
        self._queue: asyncio.PriorityQueue[tuple[int, int, str, Any]] = asyncio.PriorityQueue(maxsize=maxsize)
        self._seq = itertools.count()  # Monotonic counter for stable FIFO within same priority
        # event_name -> list of async callback functions
        self._subscribers: Dict[str, List[Callable[[str, Any], Coroutine[Any, Any, None]]]] = {}
        # Wildcard subscribers that listen to all events
        self._all_subscribers: List[Callable[[str, Any], Coroutine[Any, Any, None]]] = []
        self._running = False
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Start the background consumer task."""
        if self._running:
            return
        self._loop = loop or asyncio.get_event_loop()
        self._running = True
        self._worker_task = asyncio.create_task(self._dispatch_loop())
        logger.info("EventBus started with priority queue worker.")

    async def stop(self, timeout: float = 3.0) -> None:
        """Gracefully drain pending queue items and stop the event bus."""
        if not self._running:
            return
        self._running = False
        try:
            if not self._queue.empty():
                await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug("EventBus stop drain ended or timed out: %s", e)

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("EventBus stopped.")

    def subscribe(
        self,
        event_name: str,
        callback: Callable[[str, Any], Coroutine[Any, Any, None]],
    ) -> None:
        """Register an async callback for a specific event or '*' for all events."""
        if event_name == "*":
            if callback not in self._all_subscribers:
                self._all_subscribers.append(callback)
        else:
            if event_name not in self._subscribers:
                self._subscribers[event_name] = []
            if callback not in self._subscribers[event_name]:
                self._subscribers[event_name].append(callback)

    def unsubscribe(
        self,
        event_name: str,
        callback: Callable[[str, Any], Coroutine[Any, Any, None]],
    ) -> None:
        """Remove a previously registered subscriber."""
        if event_name == "*":
            if callback in self._all_subscribers:
                self._all_subscribers.remove(callback)
        elif event_name in self._subscribers:
            if callback in self._subscribers[event_name]:
                self._subscribers[event_name].remove(callback)

    async def publish(
        self,
        event_name: str,
        payload: Any = None,
        priority: EventPriority = EventPriority.MEDIUM,
    ) -> None:
        """Publish an event asynchronously into the priority queue."""
        seq = next(self._seq)
        await self._queue.put((int(priority), seq, event_name, payload))

    def publish_nowait(
        self,
        event_name: str,
        payload: Any = None,
        priority: EventPriority = EventPriority.MEDIUM,
    ) -> bool:
        """Thread-safe synchronous enqueue (for callbacks from broker threads)."""
        seq = next(self._seq)
        item = (int(priority), seq, event_name, payload)
        try:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._queue.put_nowait, item)
            else:
                self._queue.put_nowait(item)
            return True
        except (asyncio.QueueFull, RuntimeError) as e:
            logger.warning("EventBus queue full or loop closed, dropped event %s: %s", event_name, e)
            return False

    async def _dispatch_loop(self) -> None:
        """Continuous worker that dequeues and executes events in strict priority order."""
        while self._running:
            try:
                priority, _, event_name, payload = await self._queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error retrieving from EventBus queue: %s", e)
                continue

            # Gather target callbacks
            handlers = list(self._subscribers.get(event_name, [])) + list(self._all_subscribers)
            if not handlers:
                self._queue.task_done()
                continue

            for handler in handlers:
                try:
                    # Critical events are awaited directly to maintain deterministic ordering;
                    # normal/low events can be awaited or scheduled
                    if priority == EventPriority.HIGH:
                        await asyncio.wait_for(handler(event_name, payload), timeout=2.0)
                    else:
                        asyncio.create_task(self._safe_invoke(handler, event_name, payload))
                except asyncio.TimeoutError:
                    logger.warning("HIGH priority handler timed out (2.0s) for event %s: %s", event_name, handler)
                except Exception as exc:
                    logger.error("Error executing handler for %s: %s", event_name, exc)

            self._queue.task_done()

    @staticmethod
    async def _safe_invoke(
        handler: Callable[[str, Any], Coroutine[Any, Any, None]],
        event_name: str,
        payload: Any,
    ) -> None:
        """Safely invoke an asynchronous handler without crashing the event bus."""
        try:
            await handler(event_name, payload)
        except Exception as exc:
            logger.error("Unhandled exception in EventBus subscriber for %s: %s", event_name, exc)

    @property
    def pending_count(self) -> int:
        """Number of events currently waiting in the queue."""
        return self._queue.qsize()
