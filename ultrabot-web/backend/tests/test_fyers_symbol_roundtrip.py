"""v0.4.8 regression tests — hotfixes #2 and #4 (Fyers symbol round-trip).

Background: FyersBroker.get_candles() used to blindly reformat whatever
symbol it received into f"{exchange}:{symbol}-EQ". When callers (the live
FyersCandleFeed and the backtest primary history source) passed an
already-formatted identifier produced by to_fyers_symbol() — e.g.
"NSE:SBIN-EQ" — the broker sent "NSE:NSE:SBIN-EQ-EQ" to Fyers, which
rejected it with -300 "Invalid symbol provided". Both realtime feed and
backtest silently degraded to Yahoo while tests stayed green, because the
unit tests only covered the string mapper, never the broker payload.

These tests pin the broker-level payload contract with a mocked SDK client:
  - raw engine symbol  ("SBIN")            → "NSE:SBIN-EQ"
  - pre-formatted      ("NSE:SBIN-EQ")     → unchanged
  - pre-formatted index ("NSE:NIFTY50-INDEX") → unchanged
  - SDK missing                              → actionable RuntimeError
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from brokers.fyers import FyersBroker, _FYERS_SDK_AVAILABLE


def _capture_history_payload(monkeypatch, fake_response=None):
    """Patch FyersBroker._get_client with a stub whose .history() records its
    payload and returns a canned Fyers 'ok' response. Returns (broker, payloads)."""
    payloads = []

    def _fake_history(payload):
        payloads.append(payload)
        return fake_response or {
            "s": "ok",
            "candles": [
                [1787629500, 1036.0, 1036.0, 1031.3, 1033.0, 47130],
                [1787629560, 1033.0, 1034.2, 1032.5, 1034.0, 38211],
            ],
        }

    stub_client = SimpleNamespace(history=_fake_history)
    monkeypatch.setattr(FyersBroker, "_get_client", lambda self: stub_client, raising=True)
    broker = FyersBroker(app_id="TESTAPP-100", access_token="tok", secret_key="sec")
    return broker, payloads


@pytest.mark.asyncio
async def test_raw_symbol_gets_eq_suffix(monkeypatch):
    """Raw engine symbol 'SBIN' must be sent as 'NSE:SBIN-EQ'."""
    broker, payloads = _capture_history_payload(monkeypatch)
    candles = await broker.get_candles("SBIN", resolution="1", range_from="2026-08-25", range_to="2026-08-29")
    assert len(candles) == 2
    assert payloads[0]["symbol"] == "NSE:SBIN-EQ"


@pytest.mark.asyncio
async def test_preformatted_symbol_is_not_double_prefixed(monkeypatch):
    """Pre-formatted 'NSE:SBIN-EQ' must pass through UNCHANGED (hotfix #2)."""
    broker, payloads = _capture_history_payload(monkeypatch)
    candles = await broker.get_candles("NSE:SBIN-EQ", resolution="1", range_from="2026-08-25", range_to="2026-08-29")
    assert len(candles) == 2
    assert payloads[0]["symbol"] == "NSE:SBIN-EQ"  # NOT "NSE:NSE:SBIN-EQ-EQ"


@pytest.mark.asyncio
async def test_preformatted_index_symbol_unchanged(monkeypatch):
    """Index identifiers like 'NSE:NIFTY50-INDEX' must pass through unchanged."""
    broker, payloads = _capture_history_payload(monkeypatch)
    await broker.get_candles("NSE:NIFTY50-INDEX", resolution="1", range_from="2026-08-25", range_to="2026-08-29")
    assert payloads[0]["symbol"] == "NSE:NIFTY50-INDEX"


@pytest.mark.asyncio
async def test_lowercase_preformatted_symbol_normalized(monkeypatch):
    """Case/whitespace variants of formatted symbols are normalized, not mangled."""
    broker, payloads = _capture_history_payload(monkeypatch)
    await broker.get_candles(" nse:sbin-eq ", resolution="1", range_from="2026-08-25", range_to="2026-08-29")
    assert payloads[0]["symbol"] == "NSE:SBIN-EQ"


def test_sdk_missing_raises_actionable_error(monkeypatch):
    """With the SDK absent, build_auth_url must raise a RuntimeError that
    tells the user HOW to fix it (hotfix #4) — not a cryptic AttributeError."""
    import brokers.fyers as fyers_module

    monkeypatch.setattr(fyers_module, "_FYERS_SDK_AVAILABLE", False)
    monkeypatch.setattr(fyers_module, "fyersModel", None)
    with pytest.raises(RuntimeError) as exc_info:
        FyersBroker.build_auth_url(app_id="X-100", redirect_uri="http://127.0.0.1/cb")
    msg = str(exc_info.value)
    assert "pip install --no-deps -r requirements-fyers.txt" in msg


@pytest.mark.skipif(not _FYERS_SDK_AVAILABLE, reason="fyers-apiv3 not installed")
def test_real_to_fyers_symbol_contract():
    """Pin the mapper contract the broker round-trip depends on."""
    from feeds.fyers_candles import to_fyers_symbol

    assert to_fyers_symbol("SBIN") == "NSE:SBIN-EQ"
    assert to_fyers_symbol("NSE:SBIN-EQ") == "NSE:SBIN-EQ"
    assert to_fyers_symbol("NIFTY") == "NSE:NIFTY50-INDEX"
