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
    """Non-blocking, thread-safe asynchronous priority event bus with loop-bound lifecycle."""

    def __init__(self, maxsize: int = 10000):
        self._maxsize = maxsize
        self._queue: Optional[asyncio.PriorityQueue[tuple[int, int, str, Any]]] = None
        self._seq = itertools.count()  # Monotonic counter for stable FIFO within same priority
        # event_name -> list of async callback functions
        self._subscribers: Dict[str, List[Callable[[str, Any], Coroutine[Any, Any, None]]]] = {}
        # Wildcard subscribers that listen to all events
        self._all_subscribers: List[Callable[[str, Any], Coroutine[Any, Any, None]]] = []
        self._running = False
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._background_tasks: Set[asyncio.Task[Any]] = set()

    def _get_queue(self) -> asyncio.PriorityQueue[tuple[int, int, str, Any]]:
        """Get or lazily initialize the priority queue bound to active loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._queue is None:
            self._queue = asyncio.PriorityQueue(maxsize=self._maxsize)
            self._loop = current_loop
        elif self._loop is not None and current_loop is not None and self._loop is not current_loop:
            # Rebind queue to active running loop if loop changed between tests
            logger.debug("EventBus detected loop change; reinitializing priority queue")
            self._queue = asyncio.PriorityQueue(maxsize=self._maxsize)
            self._loop = current_loop
        return self._queue

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Start the background consumer task."""
        if self._running and self._worker_task and not self._worker_task.done():
            return
        try:
            self._loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self._loop = loop or asyncio.get_event_loop()
        self._get_queue()
        self._running = True
        self._worker_task = asyncio.create_task(self._dispatch_loop())
        logger.info("EventBus started with priority queue worker.")

    async def stop(self, timeout: float = 3.0) -> None:
        """Gracefully drain pending queue items, cancel workers, and stop the event bus."""
        if not self._running:
            return
        self._running = False
        queue = self._queue
        if queue is not None:
            try:
                if not queue.empty():
                    await asyncio.wait_for(queue.join(), timeout=timeout)
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug("EventBus stop drain ended or timed out: %s", e)

        # Cancel main dispatch worker
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._worker_task = None

        # Clean up any pending medium/low background handler tasks
        if self._background_tasks:
            pending = [t for t in self._background_tasks if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._background_tasks.clear()

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
        queue = self._get_queue()
        await queue.put((int(priority), seq, event_name, payload))

    def publish_nowait(
        self,
        event_name: str,
        payload: Any = None,
        priority: EventPriority = EventPriority.MEDIUM,
    ) -> bool:
        """Thread-safe synchronous enqueue (for callbacks from broker threads)."""
        seq = next(self._seq)
        item = (int(priority), seq, event_name, payload)
        queue = self._get_queue()
        try:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(queue.put_nowait, item)
            else:
                queue.put_nowait(item)
            return True
        except (asyncio.QueueFull, RuntimeError) as e:
            logger.warning("EventBus queue full or loop closed, dropped event %s: %s", event_name, e)
            return False

    async def _dispatch_loop(self) -> None:
        """Continuous worker that dequeues and executes events in strict priority order."""
        queue = self._get_queue()
        while self._running:
            try:
                priority, _, event_name, payload = await queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error retrieving from EventBus queue: %s", e)
                queue = self._get_queue()
                await asyncio.sleep(0.1)
                continue

            # Gather target callbacks
            handlers = list(self._subscribers.get(event_name, [])) + list(self._all_subscribers)
            if not handlers:
                queue.task_done()
                continue

            for handler in handlers:
                try:
                    # Critical events are awaited directly to maintain deterministic ordering;
                    # normal/low events can be scheduled with background tracking
                    if priority == EventPriority.HIGH:
                        await asyncio.wait_for(handler(event_name, payload), timeout=2.0)
                    else:
                        task = asyncio.create_task(self._safe_invoke(handler, event_name, payload))
                        self._background_tasks.add(task)
                        task.add_done_callback(self._background_tasks.discard)
                except asyncio.TimeoutError:
                    logger.warning("HIGH priority handler timed out (2.0s) for event %s: %s", event_name, handler)
                except Exception as exc:
                    logger.error("Error executing handler for %s: %s", event_name, exc)

            queue.task_done()

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
        if self._queue is None:
            return 0
        return self._queue.qsize()
