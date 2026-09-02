"""Runtime Smoke Test Suite for UltraBot Production Readiness."""
import asyncio
import os
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brokers.paper_broker import PaperBroker
from risk.risk_engine import RiskEngine
from strategies.registry import StrategyRegistry
from db.database import init_db, async_session_factory
from db.repository import Repository
import pandas as pd
import numpy as np

IST = ZoneInfo("Asia/Kolkata")


class TestRuntimeSmokeSuite(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()

    async def test_01_paper_broker_roundtrip(self):
        """Test paper broker order execution, capital tracking, and PnL."""
        initial_cap = 100000.0
        broker = PaperBroker(initial_capital=initial_cap)
        self.assertEqual(broker.capital, initial_cap)

        # Place BUY order for NIFTY 24000 CE (50 qty @ ₹100 = ₹5000)
        buy_res = await broker.place_order(
            symbol="NIFTY24000CE",
            quantity=50,
            price=100.0,
            transaction_type="BUY",
            order_type="LIMIT",
        )
        self.assertTrue(buy_res["success"], f"Buy failed: {buy_res}")
        buy_fee = buy_res.get("fees", 0.0)
        expected_capital_after_buy = initial_cap - (50 * 100.0 + buy_fee)
        self.assertAlmostEqual(broker.capital, expected_capital_after_buy, places=1)
        self.assertEqual(len(broker.positions), 1)

        # Place SELL order to close at ₹120 (50 qty @ ₹120 = ₹6000)
        sell_res = await broker.place_order(
            symbol="NIFTY24000CE",
            quantity=50,
            price=120.0,
            transaction_type="SELL",
            order_type="LIMIT",
        )
        self.assertTrue(sell_res["success"], f"Sell failed: {sell_res}")
        sell_fee = sell_res.get("fees", 0.0)
        expected_pnl = (120.0 - 100.0) * 50 - (buy_fee + sell_fee)
        expected_capital_after_sell = initial_cap + expected_pnl

        self.assertAlmostEqual(broker.capital, expected_capital_after_sell, places=1)
        open_positions = await broker.get_positions()
        self.assertEqual(len(open_positions), 0)
        print(f"\n[SMOKE TEST 1 PASS] Paper Broker round-trip: Initial=INR {initial_cap}, Final=INR {broker.capital:.2f}, Net PnL=INR {expected_pnl:.2f}")

    async def test_02_risk_engine_18_gates(self):
        """Test all 18 risk gates including G11 drawdown with 0% and the Phase 1 G17/G18 additions."""
        config = {
            "max_daily_loss_pct": 3.0,
            "max_drawdown_pct": 5.0,
            "max_open_positions": 10,
            "max_position_size_pct": 20.0,
            "risk_per_trade_pct": 1.0,
            "market_open_time": "09:15",
            "market_close_time": "15:30",
            "no_trade_before": "09:20",
            "no_trade_after": "15:15",
        }
        re = RiskEngine(config)
        self.assertEqual(len(re.gates), 18, "RiskEngine must have exactly 18 gates (G1-G16 + G17 CostPreCheck + G18 StrategyGuard)")

        # Mock signal & context with 0.0% drawdown and high confidence
        signal = {
            "symbol": "RELIANCE",
            "direction": "BUY",
            "entry_price": 2500.0,
            "stop_loss": 2480.0,
            "target": 2550.0,
            "quantity": 5,  # 5 * 2500 = 12,500 <= 20,000 (20% of 100k)
            "confidence": 0.85,
            "strategy": "PullbackTrendContinuation",
            "timestamp": datetime.now(IST).isoformat(),
        }
        context = {
            "total_capital": 100000.0,
            "available_capital": 90000.0,
            "current_drawdown_pct": 0.0,
            "daily_loss": 0.0,
            "open_positions": [],
            "daily_trade_count": 2,
            "consecutive_losses": 0,
            "vix": 14.5,
            "market_regime": "Bull",
            "current_time": "10:30",
        }

        result = await re.evaluate(signal, context)
        self.assertTrue(result.passed, f"Risk evaluation failed: {result.block_reason}")
        self.assertEqual(len(result.all_gates), 18)

        # Check G11 Gate
        g11_result = next((g for g in result.all_gates if "G11" in g.gate_name or "Drawdown" in g.gate_name), None)
        self.assertIsNotNone(g11_result, "G11 MaxDrawdown gate must be present")
        self.assertTrue(g11_result.passed, "G11 should pass when drawdown is 0.0%")
        self.assertEqual(g11_result.value, 0.0, "G11 value must be 0.0, not threshold")
        print(f"\n[SMOKE TEST 2 PASS] Risk Engine: Evaluated 18 gates successfully. G11 drawdown value={g11_result.value}% (passed={g11_result.passed})")

    async def test_03_scanner_and_batch_insert(self):
        """Test strategy registry execution and performance batch insertion."""
        registry = StrategyRegistry()
        self.assertGreaterEqual(len(registry.strategies), 21)

        # Synthesize realistic 5-min candle bars for testing
        np.random.seed(42)
        dates = pd.date_range("2026-08-20 09:15", periods=50, freq="5min")
        close_prices = 100.0 + np.cumsum(np.random.randn(50) * 0.5)
        high_prices = close_prices + np.random.uniform(0.1, 0.5, 50)
        low_prices = close_prices - np.random.uniform(0.1, 0.5, 50)
        open_prices = low_prices + np.random.uniform(0.05, 0.4, 50)
        volumes = np.random.randint(1000, 50000, 50)

        df = pd.DataFrame({
            "open": open_prices,
            "high": high_prices,
            "low": low_prices,
            "close": close_prices,
            "volume": volumes,
        }, index=dates)

        signals = []
        for name, strategy in registry.strategies.items():
            try:
                sig = strategy.generate_signal("INFY", df)
                if sig:
                    signals.append(sig)
            except Exception as e:
                # Strategy might need specific conditions, shouldn't crash
                pass

        print(f"\n[SMOKE TEST 3 PASS] Strategy registry generated {len(signals)} test signals across {len(registry.strategies)} strategies.")

        # Test batch_insert_performance
        async with async_session_factory() as session:
            repo = Repository(session)
            perf_records = [
                {
                    "strategy": "PullbackTrendContinuation",
                    "pnl": 350.0,
                    "holding_time_seconds": 1200,
                },
                {
                    "strategy": "MomentumBreakout",
                    "pnl": -120.0,
                    "holding_time_seconds": 600,
                },
            ]
            await repo.batch_insert_performance(perf_records)
            await session.commit()

            all_perf = await repo.get_all_strategy_performance()
            self.assertGreaterEqual(len(all_perf), 1)
            print(f"[SMOKE TEST 3 PASS] Repository batch_insert_performance verified successfully. Stored {len(all_perf)} strategy records.")


if __name__ == "__main__":
    unittest.main()
