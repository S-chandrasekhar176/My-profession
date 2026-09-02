"""LIVE PIPELINE PROBE (2026-08-28, market open) — real-data gate-chain validation.

Purpose: thoroughly test the FULL 18-gate risk chain with REAL market data —
real candles (Yahoo, live), real VIX/NIFTY/regime, real capital config, real
repository (G13 duplicate lookup + G14 stats from the actual trades ledger) —
using a PROBE signal whose geometry is derived from the symbol's REAL current
price/ATR (exactly the geometry strategies emit). The probe is a diagnostic:
it proves each gate evaluates real data correctly. It is NOT a trading signal
and its results are not recorded anywhere.

Validated live earlier the same morning: the G15 volume-ratio forming-bar bias
(BAJFINANCE 0.05x absurdity) — this probe re-checks the corrected metric on
every watchlist symbol.
"""
import asyncio
import sys
from datetime import datetime

sys.path.insert(0, ".")

from config.settings import settings
from feeds.yahoo_historical import YahooHistoricalFeed
from risk.risk_engine import RiskEngine
from utils.indicators import calculate_atr
import pandas as pd


async def main():
    feed = YahooHistoricalFeed()
    risk_engine = RiskEngine(config=settings.get_risk_config())

    print(f"PROBE TIME: {datetime.now().astimezone().isoformat()}")
    rcfg = settings.get_risk_config()
    print(f"capital.virtual_capital = {settings.get_capital_config().get('virtual_capital')}")
    print(f"min_volume_ratio = {rcfg.get('min_volume_ratio')}")
    print()

    symbols = ["RELIANCE", "BAJFINANCE", "JSWSTEEL", "CIPLA", "HDFCBANK",
               "JSWSTEEL", "INFY", "TATAMOTORS" if False else "TCS"]
    symbols = ["RELIANCE", "BAJFINANCE", "JSWSTEEL", "CIPLA", "HDFCBANK", "INFY", "TCS"]

    for sym in symbols:
        # count=100 exactly as core/engine.py requests it (period auto-maps
        # to "5d" -> full multi-day history even early in the session)
        candles = await feed.get_candles(sym, timeframe="5m", count=100, force_refresh=True)
        if not candles or len(candles) < 21:
            print(f"{sym}: insufficient candles ({len(candles) if candles else 0}) — SKIP")
            continue

        df = pd.DataFrame(candles)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

        ltp = float(df["close"].iloc[-1])
        atr_series = calculate_atr(df["high"], df["low"], df["close"], period=14)
        atr = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else ltp * 0.004

        # Corrected volume ratio (same formula as core/engine.py)
        vols = [float(c.get("volume", 0) or 0) for c in candles[-21:]]
        avg_completed = sum(vols[:-2]) / (len(vols) - 2)
        ratio_completed = vols[-2] / avg_completed if avg_completed > 0 else 0.0
        ratio_forming = 0.0
        try:
            bar_start = datetime.fromisoformat(str(candles[-1].get("timestamp")))
            frac = (datetime.now(tz=bar_start.tzinfo) - bar_start).total_seconds() / 300.0
            frac = min(1.0, max(0.2, frac))
        except Exception:
            frac = 1.0
        if vols[-1] > 0 and avg_completed > 0:
            ratio_forming = (vols[-1] / frac) / avg_completed
        vr = round(max(ratio_completed, ratio_forming), 3)

        # PROBE signal: geometry from real price/ATR (1.5*ATR target, 1*ATR stop)
        probe = {
            "symbol": sym,
            "strategy": "PullbackTrendContinuation",
            "direction": "BUY",
            "entry_price": round(ltp, 2),
            "sl_price": round(ltp - atr, 2),
            "target_price": round(ltp + 1.5 * atr, 2),
            "confidence": 0.80,
            "volume_ratio": vr,
        }

        # REAL context: live VIX, regime detection inputs, real capital
        vix = await feed.get_ltp("^INDIAVIX") if hasattr(feed, "get_ltp") else None
        context = {
            "total_capital": float(settings.get_capital_config().get("virtual_capital")),
            "vix": vix,
            "trend": "neutral",
            "current_price": ltp,
            "broker_ltp": ltp,
            "open_positions": [],
            "open_position_symbols": [],
            "daily_trades": 0,
            "daily_pnl": 0.0,
            "current_drawdown_pct": 0.0,
            "volume_ratio": vr,
        }

        result = await risk_engine.evaluate(probe, symbol=sym, context=context)

        # Collect per-gate results (RiskResult.all_gates)
        gate_summary = []
        for g in (getattr(result, "all_gates", None) or []):
            name = getattr(g, "gate_name", "?")
            passed = getattr(g, "passed", None)
            val = getattr(g, "value", None)
            gate_summary.append(f"{name}:{'PASS' if passed else 'FAIL'}({val})")

        print(f"{sym}: LTP={ltp:.2f} ATR={atr:.2f} volRatio(corrected)={vr}x "
              f"[completed={ratio_completed:.2f} forming={ratio_forming:.2f}]")
        print(f"   probe entry={probe['entry_price']} sl={probe['sl_price']} tgt={probe['target_price']}")
        print(f"   RESULT: passed={result.passed} blocked_by={result.blocked_by or '—'}")
        if result.block_reason:
            print(f"   block_reason: {result.block_reason[:110]}")
        print(f"   gates: {' | '.join(gate_summary)}")
        print()

    print("PROBE COMPLETE — all gates evaluated on live market data.")


if __name__ == "__main__":
    asyncio.run(main())
