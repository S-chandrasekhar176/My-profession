"""Regression tests for HOTFIX #5 (2026-09-01 live session).

During the 08:45 IST pre-market watchlist build, Fyers returned 429
"request limit reached" for 2/51 history calls even though our client-side
limiter admitted exactly the documented 10 req/s. Root cause: Fyers'
server-side window is stricter than documented (burst intolerance), so
running at the documented cap produces occasional 429s under concurrent load.

Fix: both Fyers limiters now run at 8 req/s (20% headroom below the
documented 10 req/s cap). These tests pin that behavior so the headroom
cannot silently regress to the burst-unsafe 10/s config.
"""
import asyncio
import time

import pytest

from brokers.fyers import _data_limiter, _transactional_limiter
from core.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_fyers_limiters_configured_with_headroom():
    """Pin HOTFIX #5: Fyers limiters run at 8/s, not the documented 10/s."""
    assert _data_limiter.per_second == 8, (
        "Fyers data limiter must keep 20% headroom below documented 10/s — "
        "10/s caused live 429s during 08:45 watchlist build (hotfix #5)"
    )
    assert _transactional_limiter.per_second == 8, (
        "Fyers transactional limiter must keep 20% headroom below documented 10/s"
    )
    # Per-minute/day caps stay at documented values
    assert _data_limiter.per_minute == 200
    assert _transactional_limiter.per_minute == 200
    assert _data_limiter.per_day == 100_000
    assert _transactional_limiter.per_day == 10_000


@pytest.mark.asyncio
async def test_limiter_never_admits_more_than_per_second_under_concurrency():
    """Simulates the 08:45 burst: 51 concurrent fetches must be throttled so
    that no sliding 1-second window ever contains more than per_second events."""
    limiter = RateLimiter(per_second=8, per_minute=200, name="test-burst")

    async def acquire_one():
        await limiter.acquire(timeout=30.0)

    await asyncio.gather(*[acquire_one() for _ in range(51)])

    # All 51 admitted events must obey the sliding 1s cap.
    # NOTE: window check must be bounded on BOTH sides — events newer than
    # events[i] produce negative (signed) differences that would falsely
    # count as "inside the window" (this bit me in the first draft).
    events = sorted(limiter._events)
    assert len(events) == 51
    for i in range(len(events)):
        window_count = sum(1 for t in events if 0 <= events[i] - t < 1.0)
        assert window_count <= 8, (
            f"sliding 1s window ending at offset {i} admitted {window_count} "
            f"events (cap is 8) — burst-unsafe configuration regressed"
        )


@pytest.mark.asyncio
async def test_limiter_throttling_completes_within_expected_time():
    """51 fetches at 8/s must complete in ~6-7s (ceil(51/8) seconds), proving
    the watchlist build self-heals into a slightly longer, safe scan instead
    of failing 429 calls."""
    limiter = RateLimiter(per_second=8, per_minute=200, name="test-timing")
    start = time.monotonic()
    await asyncio.gather(*[limiter.acquire(timeout=30.0) for _ in range(51)])
    elapsed = time.monotonic() - start
    # 51 requests at 8/s => 7 seconds minimum (6 full windows of 8 + 1)
    assert 6.0 <= elapsed <= 15.0, f"throttled batch took {elapsed:.2f}s — unexpected"
    # And the shared limiter status reports sane counters afterwards
    status = limiter.status()
    assert status["used_last_minute"] == 51
