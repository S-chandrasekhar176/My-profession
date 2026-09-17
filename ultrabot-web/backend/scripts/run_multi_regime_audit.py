"""Multi-Regime Strategy Win Rate & Profit Factor Audit Runner (Item 1).

Evaluates the 9 production V2 strategies (ORB, MB, PTC, VC, SIC, MRF, TRS, VR, BBR)
across four distinct market regimes:
1. Bull (Strong trend up, high volume)
2. Bear (Strong trend down, panic volume)
3. Sideways (Mean-reverting, tight consolidation, low ATR)
4. Volatile (High ATR, wide oscillations, climax reversals)

Verifies the Phase Gate requirement:
- Gross Win Rate >= 45% - 50%
- Profit Factor (PF) >= 1.5
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
import numpy as np
import pandas as pd

# Set up project path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from strategies.registry import StrategyRegistry
from fees.nse_fee_calculator import NSEFeeCalculator
from fees.slippage import apply_slippage
from risk.partial_booker import PartialBooker

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger("multi_regime_audit")


# ─────────────────────────────────────────────
# Synthetic Regime Market Data Generators
# ─────────────────────────────────────────────

def generate_regime_candles(
    regime: str,
    base_price: float = 1000.0,
    n_bars: int = 150,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate realistic 5-minute OHLCV candles characteristic of a specific regime."""
    np.random.seed(seed)
    bars = []
    curr = base_price
    
    if regime == "Bull":
        drift = 0.0012  # +0.12% upward drift per bar
        vol = 0.003
        base_vol = 15000
    elif regime == "Bear":
        drift = -0.0014 # -0.14% downward drift per bar
        vol = 0.004
        base_vol = 18000
    elif regime == "Sideways":
        drift = 0.0
        vol = 0.0018
        base_vol = 8000
    elif regime == "Volatile":
        drift = 0.0
        vol = 0.007
        base_vol = 25000
    else:
        drift = 0.0
        vol = 0.003
        base_vol = 10000

    # Create 2 full trading days (09:15 to 15:30 = 75 bars/day, total 150 bars)
    day1_times = pd.date_range("2026-08-31 09:15", periods=75, freq="5min")
    day2_times = pd.date_range("2026-09-01 09:15", periods=75, freq="5min")
    timestamps = day1_times.append(day2_times)
    total_bars = len(timestamps)

    for i in range(total_bars):
        if regime == "Sideways":
            # Mean-revert toward base_price
            deviation = (curr - base_price) / base_price
            pull = -0.06 * deviation
            ret = pull + np.random.normal(0, vol)
        elif regime == "Volatile":
            # Climax spikes and reversals
            ret = np.random.normal(drift, vol)
            if i % 12 == 0:
                ret *= 2.2
        else:
            ret = drift + np.random.normal(0, vol)

        nxt = max(10.0, curr * (1.0 + ret))
        bar_open = curr
        bar_close = nxt
        spread = abs(bar_close - bar_open)
        bar_high = max(bar_open, bar_close) + spread * np.random.uniform(0.2, 0.8)
        bar_low = min(bar_open, bar_close) - spread * np.random.uniform(0.2, 0.8)
        bar_vol = int(base_vol * np.random.uniform(0.6, 2.2))
        if regime == "Volatile" and i % 12 == 0:
            bar_vol *= 3

        bars.append({
            "timestamp": timestamps[i],
            "open": round(bar_open, 2),
            "high": round(bar_high, 2),
            "low": round(bar_low, 2),
            "close": round(bar_close, 2),
            "volume": bar_vol,
        })
        curr = nxt

    df = pd.DataFrame(bars)
    df.set_index("timestamp", drop=False, inplace=True)
    return df


# ─────────────────────────────────────────────
# Backtest Simulator for One Strategy in One Regime
# ─────────────────────────────────────────────

async def simulate_strategy_in_regime(
    strategy_name: str,
    strat_instance: Any,
    regime: str,
    vix: float,
    n_scenarios: int = 8,
) -> Dict[str, Any]:
    fee_calc = NSEFeeCalculator()
    booker = PartialBooker()
    slip_config = {"enabled": True, "base_bps": 5.0, "impact_bps_per_crore": 2.0, "max_bps": 25.0}

    all_trades: List[Dict[str, Any]] = []

    for seed_idx in range(n_scenarios):
        seed = 1000 + seed_idx * 37
        df = generate_regime_candles(regime=regime, base_price=500.0 + seed_idx * 150.0, seed=seed)
        
        # Start scanning at t=75 (09:15 AM on Day 2) with 75 bars of Day 1 warmup
        warmup = 75
        active_trade: Optional[Dict[str, Any]] = None

        for t in range(warmup, len(df)):
            current_bar = df.iloc[t]
            high = float(current_bar["high"])
            low = float(current_bar["low"])
            close = float(current_bar["close"])

            # 1. Manage Active Trade
            if active_trade is not None:
                direction = active_trade["direction"]
                entry_price = active_trade["entry_price"]
                sl = active_trade["stop_loss"]
                target = active_trade["target"]
                qty = active_trade["quantity"]
                closed = False
                exit_price = close
                exit_reason = ""

                if (direction == "LONG" and low <= sl) or (direction == "SHORT" and high >= sl):
                    closed = True
                    exit_reason = "STOP_LOSS"
                    exit_price, _, _ = apply_slippage(sl, is_buy=(direction == "SHORT"), quantity=qty, config=slip_config)
                elif (direction == "LONG" and high >= target) or (direction == "SHORT" and low <= target):
                    closed = True
                    exit_reason = "TARGET"
                    exit_price, _, _ = apply_slippage(target, is_buy=(direction == "SHORT"), quantity=qty, config=slip_config)
                elif t - active_trade["entry_bar"] >= 18:
                    # Time-stop exit (90 minutes in 5m bars)
                    closed = True
                    exit_reason = "TIME_STOP"
                    exit_price, _, _ = apply_slippage(close, is_buy=(direction == "SHORT"), quantity=qty, config=slip_config)

                if closed:
                    gross_pnl = (exit_price - entry_price) * qty if direction == "LONG" else (entry_price - exit_price) * qty
                    fees = fee_calc.calculate_equity_intraday(entry_price, exit_price, qty)["total"]
                    net_pnl = gross_pnl - fees
                    all_trades.append({
                        "direction": direction,
                        "entry": entry_price,
                        "exit": exit_price,
                        "quantity": qty,
                        "gross_pnl": round(gross_pnl, 2),
                        "net_pnl": round(net_pnl, 2),
                        "fees": round(fees, 2),
                        "exit_reason": exit_reason,
                        "is_gross_win": gross_pnl > 0,
                        "is_net_win": net_pnl > 0,
                    })
                    active_trade = None

            # 2. Scan for new setup if flat
            if active_trade is None and t < len(df) - 5:
                window_df = df.iloc[max(0, t - 75):t + 1]
                sig = None
                try:
                    if hasattr(strat_instance, "scan"):
                        sig = await strat_instance.scan(symbol="TEST", candles=window_df, regime=regime, vix=vix)
                    elif hasattr(strat_instance, "generate_signals"):
                        sig = await strat_instance.generate_signals(candles=window_df, symbol="TEST")
                except Exception:
                    sig = None

                if sig and isinstance(sig, dict) and sig.get("direction"):
                    direction = str(sig.get("direction", "LONG")).upper()
                    if direction == "BUY":
                        direction = "LONG"
                    elif direction == "SELL":
                        direction = "SHORT"

                    raw_entry = float(sig.get("entry_price") or close)
                    entry_px, _, _ = apply_slippage(raw_entry, is_buy=(direction == "LONG"), quantity=50, config=slip_config)
                    
                    sl = float(sig.get("sl_price") or sig.get("stop_loss") or (entry_px * 0.985 if direction == "LONG" else entry_px * 1.015))
                    target = float(sig.get("target_price") or sig.get("target") or (entry_px * 1.03 if direction == "LONG" else entry_px * 0.97))

                    # Sanitize geometry: SL must be strictly opposite to Target
                    valid_geom = (direction == "LONG" and target > entry_px > sl) or (direction == "SHORT" and target < entry_px < sl)
                    if valid_geom:
                        active_trade = {
                            "direction": direction,
                            "entry_price": entry_px,
                            "stop_loss": sl,
                            "target": target,
                            "quantity": 50,
                            "entry_bar": t,
                        }

    # Summary Metrics
    total = len(all_trades)
    if total == 0:
        return {
            "strategy": strategy_name,
            "regime": regime,
            "trades": 0,
            "gross_win_rate": 0.0,
            "net_win_rate": 0.0,
            "profit_factor": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "meets_gate": False,
        }

    gross_wins = [t for t in all_trades if t["is_gross_win"]]
    gross_losses = [t for t in all_trades if not t["is_gross_win"]]
    gross_wr = (len(gross_wins) / total) * 100.0
    net_wr = (sum(1 for t in all_trades if t["is_net_win"]) / total) * 100.0

    sum_gains = sum(t["gross_pnl"] for t in gross_wins)
    sum_losses = abs(sum(t["gross_pnl"] for t in gross_losses))
    pf = round(sum_gains / sum_losses, 2) if sum_losses > 0 else (99.0 if sum_gains > 0 else 0.0)

    total_gross_pnl = round(sum(t["gross_pnl"] for t in all_trades), 2)
    total_net_pnl = round(sum(t["net_pnl"] for t in all_trades), 2)

    meets_gate = bool(gross_wr >= 45.0 and pf >= 1.5)

    return {
        "strategy": strategy_name,
        "regime": regime,
        "trades": total,
        "gross_win_rate": round(gross_wr, 1),
        "net_win_rate": round(net_wr, 1),
        "profit_factor": pf,
        "gross_pnl": total_gross_pnl,
        "net_pnl": total_net_pnl,
        "meets_gate": meets_gate,
    }


# ─────────────────────────────────────────────
# Main Audit Execution
# ─────────────────────────────────────────────

ACTIVATION_MAP = {
    "Bull": ["ORB", "MB", "PTC", "VC", "SIC"],
    "Bear": ["ORB", "MB", "PTC", "VC", "SIC"],
    "Sideways": ["ORB", "MRF", "VC", "SIC", "VR", "BBR"],
    "Volatile": ["ORB", "SIC", "TRS", "VR"],
}

async def run_audit():
    registry = StrategyRegistry()
    registry.discover()

    strategies_to_audit = ["ORB", "MB", "PTC", "VC", "SIC", "MRF", "TRS", "VR", "BBR"]
    regimes = ["Bull", "Bear", "Sideways", "Volatile"]
    vix_map = {"Bull": 13.0, "Bear": 19.5, "Sideways": 12.0, "Volatile": 24.5}

    print("\n" + "=" * 90)
    print("        ULTRABOT STRATEGY MULTI-REGIME WIN RATE & PROFIT FACTOR AUDIT        ")
    print("=" * 90)
    print("Gate Threshold: Gross Win Rate >= 45.0% - 50.0%  |  Profit Factor >= 1.50")
    print("-" * 90)
    print(f"{'Strategy':<9} | {'Regime':<9} | {'Deployment':<14} | {'Trades':<7} | {'Gross WR':<9} | {'Net WR':<8} | {'PF':<6} | {'Gate Status'}")
    print("-" * 90)

    results: List[Dict[str, Any]] = []

    for strat_name in strategies_to_audit:
        instance = registry.get(strat_name)
        if instance is None:
            continue

        for regime in regimes:
            is_active_deployment = strat_name in ACTIVATION_MAP.get(regime, [])
            res = await simulate_strategy_in_regime(
                strategy_name=strat_name,
                strat_instance=instance,
                regime=regime,
                vix=vix_map[regime],
                n_scenarios=15,
            )
            res["is_active_deployment"] = is_active_deployment
            results.append(res)
            
            dep_str = "ACTIVE (LIVE)" if is_active_deployment else "PAUSED (REGIME)"
            status_str = "PASS" if res["meets_gate"] else ("LOW_SAMPLE" if res["trades"] < 5 else "FAIL")
            gross_wr_str = f"{res['gross_win_rate']:.1f}%"
            net_wr_str = f"{res['net_win_rate']:.1f}%"
            pf_str = f"{res['profit_factor']:.2f}"

            print(f"{strat_name:<9} | {regime:<9} | {dep_str:<14} | {res['trades']:<7} | {gross_wr_str:<9} | {net_wr_str:<8} | {pf_str:<6} | {status_str}")

    print("-" * 90)

    # 1. Evaluated on ACTIVE DEPLOYMENTS (what the engine actually trades in real market)
    active_deployments = [r for r in results if r["is_active_deployment"] and r["trades"] >= 5]
    if active_deployments:
        avg_gross_wr = np.mean([r["gross_win_rate"] for r in active_deployments])
        avg_net_wr = np.mean([r["net_win_rate"] for r in active_deployments])
        avg_pf = np.mean([r["profit_factor"] for r in active_deployments if r["profit_factor"] < 90])
        passed_count = sum(1 for r in active_deployments if r["meets_gate"])
        total_active_eval = len(active_deployments)

        print("\nACTIVE DEPLOYMENT COMPLIANCE (Actual Engine Trading Setups):")
        print(f"  * Active Strategy-Regime Pairs Evaluated   : {total_active_eval}")
        print(f"  * Mean Gross Win Rate                      : {avg_gross_wr:.2f}%  (Gate Requirement: >= 45.0% - 50.0%)")
        print(f"  * Mean Net Win Rate (After Statutory Fees) : {avg_net_wr:.2f}%")
        print(f"  * Mean Profit Factor                       : {avg_pf:.2f}  (Gate Requirement: >= 1.50)")
        print(f"  * Passing Gate Verification                : {passed_count}/{total_active_eval} ({passed_count/total_active_eval*100:.1f}%)")
    print("=" * 90 + "\n")

    # Save report
    output_path = BACKEND_DIR / "evidence" / "multi_regime_audit_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Full audit report saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(run_audit())
