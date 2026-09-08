"""v0.4.17 regression tests — restart resilience + Telegram 429 backoff.

Covers the post-market wave of 2026-09-08 (silent-death / restart fallout):

1. Telegram pacing: send_message serializes and paces sends — a burst can no
   longer hit the API back-to-back (the Sep-8 boot fired ~45 alerts and got
   429-flooded).
2. Telegram 429: honors parameters.retry_after, retries exactly once, and
   drops the message (with an error log) if the second attempt 429s too.
3. Pending-opportunity restore: a pending card snapshotted into its signal
   row (signal_data["opportunity"]) is re-armed into pending_opportunities on
   engine start while TTL remains and market is open; the card is
   re-broadcast so the dashboard re-renders it.
4. Restore TTL guard: a card whose expiry elapsed while the process was down
   is NOT restored (the orphan sweep expires its row honestly).
5. Market-closed guard: no restore after hours (intraday pendings are dead).
6. Orphan-sweep skip: the sweep must NOT expire signal rows that were just
   re-armed — only genuine orphans get branded EXPIRED.
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")

import notifications.telegram_bot as tg_module
from notifications.telegram_bot import TelegramBot
from core.engine import UltraBotEngine


# ────────────────────────────────────────────
# Engine harness (same pattern as v0.4.16 tests)
# ────────────────────────────────────────────

def _build_engine(repo_mock) -> UltraBotEngine:
    mock_config = MagicMock()
    mock_config.get_risk_config = MagicMock(
        return_value={"opportunity_ttl_seconds": 300, "price_mismatch_threshold_pct": 5.0}
    )
    mock_config.get_fees_config = MagicMock(return_value={})

    async def get_repo():
        return repo_mock

    engine = UltraBotEngine(
        config=mock_config,
        repository_getter=get_repo,
        error_engine=MagicMock(),
        risk_engine=MagicMock(),
        position_sizer=MagicMock(),
        partial_booker=MagicMock(),
        daily_risk_manager=MagicMock(),
        broker_factory=MagicMock(),
        feed_manager=MagicMock(),
        session_manager=MagicMock(),
    )
    engine.market_hours = MagicMock(is_market_open=MagicMock(return_value=True))
    engine._broadcast = AsyncMock()
    return engine


def _signal_row(sig_id: str, opp: dict | None) -> MagicMock:
    """Fake pending Signal row created today with an optional card snapshot."""
    sig = MagicMock()
    sig.id = sig_id
    sig.created_at = f"{datetime.now(IST):%Y-%m-%d} 10:00:00+05:30"
    sig.signal_data = {"strategy_signal": {"entry_price": 100.0}, "opportunity": opp} if opp else {}
    return sig


def _opp_card(opp_id: str, ttl_seconds_remaining: float = 120.0) -> dict:
    expiry = datetime.now(IST) + timedelta(seconds=ttl_seconds_remaining)
    return {
        "id": opp_id,
        "signal_id": "sig-1",
        "symbol": "TEST",
        "direction": "BUY",
        "strategy": "ORB",
        "expiry_at": expiry.isoformat(),
        "ttl_seconds": 300,
    }


# ────────────────────────────────────────────
# 1-2. Telegram pacing + 429 handling
# ────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {"ok": True}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_client_factory(responses: list, post_calls: list):
    """Async httpx client mock returning the queued responses in order."""

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            post_calls.append(json)
            idx = min(len(post_calls) - 1, len(responses) - 1)
            return responses[idx]

    return _FakeAsyncClient


@pytest.mark.asyncio
async def test_telegram_pacing_serializes_burst(monkeypatch):
    monkeypatch.setattr(tg_module, "_MIN_SEND_INTERVAL", 0.05)
    posts: list = []
    bot = TelegramBot(bot_token="T", chat_id="C")

    with patch.object(tg_module.httpx, "AsyncClient", _fake_client_factory([_FakeResponse()], posts)):
        started = asyncio.get_running_loop().time()
        results = await asyncio.gather(*(bot.send_message(f"m{i}") for i in range(3)))
        elapsed = asyncio.get_running_loop().time() - started

    assert all(results), "all three sends should succeed"
    assert len(posts) == 3
    # 3 paced sends: at least 2 full min-intervals must have elapsed
    assert elapsed >= 2 * 0.05, "sends must be paced, not fired back-to-back"


@pytest.mark.asyncio
async def test_telegram_429_honors_retry_after_and_recovers(monkeypatch):
    monkeypatch.setattr(tg_module, "_MIN_SEND_INTERVAL", 0.0)
    posts: list = []
    bot = TelegramBot(bot_token="T", chat_id="C")
    responses = [
        _FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}}),
        _FakeResponse(200, {"ok": True}),
    ]

    with patch.object(tg_module.httpx, "AsyncClient", _fake_client_factory(responses, posts)):
        result = await bot.send_message("survivor")

    assert result is True, "second attempt after retry_after must succeed"
    assert len(posts) == 2, "exactly one retry — no more"


@pytest.mark.asyncio
async def test_telegram_429_persistent_drops_message(monkeypatch):
    monkeypatch.setattr(tg_module, "_MIN_SEND_INTERVAL", 0.0)
    posts: list = []
    bot = TelegramBot(bot_token="T", chat_id="C")
    responses = [_FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}})]

    with patch.object(tg_module.httpx, "AsyncClient", _fake_client_factory(responses, posts)):
        result = await bot.send_message("dropped")

    assert result is False, "persistent 429 must drop the message, not loop"
    assert len(posts) == 2, "two attempts maximum"


# ────────────────────────────────────────────
# 3-5. Pending-opportunity restore
# ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restore_rearms_valid_pending_card():
    repo = MagicMock()
    sig = _signal_row("sig-1", _opp_card("opp-1"))
    repo.get_signals_by_status = AsyncMock(return_value=[sig])
    engine = _build_engine(repo)

    restored = await engine._restore_pending_opportunities()

    assert "sig-1" in restored
    assert "opp-1" in engine.pending_opportunities
    assert engine.pending_opportunities["opp-1"]["symbol"] == "TEST"
    assert engine._broadcast.await_count == 1, "card must be re-broadcast for the dashboard"
    payload = engine._broadcast.await_args.args
    assert payload[0] == "opportunity" and payload[1]["opportunity"]["id"] == "opp-1"


@pytest.mark.asyncio
async def test_restore_skips_ttl_elapsed_card():
    repo = MagicMock()
    sig = _signal_row("sig-1", _opp_card("opp-1", ttl_seconds_remaining=-30.0))
    repo.get_signals_by_status = AsyncMock(return_value=[sig])
    engine = _build_engine(repo)

    restored = await engine._restore_pending_opportunities()

    assert restored == set()
    assert "opp-1" not in engine.pending_opportunities
    engine._broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_noop_when_market_closed():
    repo = MagicMock()
    sig = _signal_row("sig-1", _opp_card("opp-1"))
    repo.get_signals_by_status = AsyncMock(return_value=[sig])
    engine = _build_engine(repo)
    engine.market_hours = MagicMock(is_market_open=MagicMock(return_value=False))

    restored = await engine._restore_pending_opportunities()

    assert restored == set()
    assert engine.pending_opportunities == {}
    repo.get_signals_by_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_ignores_legacy_row_without_snapshot():
    repo = MagicMock()
    sig = _signal_row("sig-legacy", None)  # pre-v0.4.17 row: no card snapshot
    repo.get_signals_by_status = AsyncMock(return_value=[sig])
    engine = _build_engine(repo)

    restored = await engine._restore_pending_opportunities()

    assert restored == set()
    assert engine.pending_opportunities == {}


# ────────────────────────────────────────────
# 6. Orphan sweep must skip restored rows
# ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sweep_skips_restored_ids_but_expires_others():
    repo = MagicMock()
    restored_row = _signal_row("sig-keep", _opp_card("opp-keep"))
    orphan_row = _signal_row("sig-orphan", None)
    repo.get_signals_by_status = AsyncMock(return_value=[restored_row, orphan_row])
    repo.update_signal = AsyncMock()
    engine = _build_engine(repo)

    expired = await engine._expire_orphaned_pending_signals(skip_ids={"sig-keep"})

    assert expired == 1
    expired_calls = [c.args[0] if c.args else c.kwargs.get("signal_id") for c in repo.update_signal.await_args_list]
    assert "sig-orphan" in expired_calls
    assert "sig-keep" not in expired_calls, "re-armed cards must NOT be branded expired"
