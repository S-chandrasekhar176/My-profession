import asyncio
import time
import pytest
from brokers.fyers import FyersBroker, RateLimiter

@pytest.mark.asyncio
async def test_fyers_call_timeout_returns_error():
    """Verify that a slow synchronous SDK call wrapped by FyersBroker._call
    times out after 10.0s instead of blocking indefinitely."""
    broker = FyersBroker(app_id="TEST-100", access_token="test_tok")
    limiter = RateLimiter(per_second=10)

    def slow_hanging_sdk_call():
        time.sleep(15.0)
        return {"s": "ok"}

    start = time.time()
    result = await broker._call(limiter, slow_hanging_sdk_call)
    elapsed = time.time() - start

    assert result.get("s") == "error"
    assert "timed out" in result.get("message", "").lower()
    # Should time out around 10s, not 15s
    assert elapsed < 12.0


@pytest.mark.asyncio
async def test_supervisor_staleness_logic():
    """Verify that the supervisor logic detects last_poll_seconds_ago > 180s
    and cancels the stuck task, replacing it with a new task."""
    class MockRecorder:
        def __init__(self):
            self._running = True
            self._fast_task = asyncio.create_task(asyncio.sleep(300))
            self.poll_seconds_ago = 200.0

        def get_health(self):
            return {"last_poll_seconds_ago": self.poll_seconds_ago}

        async def _fast_poll_loop(self):
            await asyncio.sleep(10)

    rec = MockRecorder()
    original_task = rec._fast_task
    assert not original_task.done()

    # Emulate supervisor check
    health = rec.get_health()
    last_poll_sec = health.get("last_poll_seconds_ago")
    is_stale = (
        last_poll_sec is not None
        and last_poll_sec > 180.0
        and rec._running
    )
    assert is_stale is True

    if is_stale:
        original_task.cancel()
        rec._fast_task = asyncio.create_task(rec._fast_poll_loop())

    await asyncio.sleep(0.01)
    assert original_task.cancelled() or original_task.cancelling()
    assert rec._fast_task != original_task
    assert not rec._fast_task.done()

    # Clean up background task
    rec._fast_task.cancel()
