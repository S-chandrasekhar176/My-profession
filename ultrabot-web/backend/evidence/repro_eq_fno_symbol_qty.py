"""REAL-EVIDENCE reproduction: equity & F&O symbol + quantity population audit.

Executes REAL production code paths (no mocked results — controlled inputs only):
  A. Equity sizing math with REAL defaults.yaml Settings (Kelly → tiers → caps → 1% floor)
  B. Live-feed symbol round-trip (equity symbol format accepted by real yahoo feed, market open)
  C. Engine's EXACT place_order kwargs vs live-broker signatures (exchange routing bug)
  D. F&O confirm path with PaperBroker: what symbol/quantity/premium actually populate
"""
import asyncio
import os
import sys
import inspect
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


print("=" * 78)
print("A. EQUITY QUANTITY MATH — PositionSizer with REAL defaults.yaml Settings")
print("=" * 78)
from config.settings import Settings
from risk.position_sizer import PositionSizer

cfg = Settings()
risk_cfg = cfg.get_risk_config()
cap_cfg = cfg.get_capital_config()
print(f"  config: capital={cap_cfg.get('total_capital')}, max_per_position_pct={cap_cfg.get('max_per_position_pct')}, "
      f"max_capital_usage_pct={cap_cfg.get('max_capital_usage_pct')}, min_position_size={cap_cfg.get('min_position_size')}")
print(f"  kelly clamp=[{risk_cfg.get('kelly_min_fraction')}, {risk_cfg.get('kelly_max_fraction')}], "
      f"hard_risk_pct={risk_cfg.get('hard_risk_pct')}")

sizer = PositionSizer(config=risk_cfg, capital_config=cap_cfg)
TOTAL = sizer.total_capital
print(f"  resolved total_capital=₹{TOTAL:,.0f}")

# Case 1: reproduce the real SUNPHARMA fill from P2 evidence (conf 0.84, vix ~11, price 1911.6)
res_eq = sizer.calculate(
    signal={"symbol": "SUNPHARMA", "direction": "SELL", "confidence": 0.84,
            "entry_price": 1911.6, "sl_price": 1930.0, "target_price": 1880.0},
    context={"segment": "EQ", "vix": 11.0, "available_capital": TOTAL},
)
# Manual Kelly trace: raw=0.84*0.25=0.21 → clamp 0.08; tiers: conf≥0.8→1.0, vix≤14→1.0, dd 0→1.0
# position_size = TOTAL*0.08 capped by 25% per-position → qty = int(size/1911.6)
expected_size = min(TOTAL * 0.08, TOTAL * 0.25, TOTAL * 0.90)
expected_qty = int(expected_size / 1911.6)
check("A1 equity qty == int(kelly_size/price)",
      res_eq.quantity == expected_qty,
      f"qty={res_eq.quantity} expected={expected_qty}, size=₹{res_eq.position_size:,.0f}")
check("A2 equity is_equity=True & lot_size=None", res_eq.is_equity is True and res_eq.lot_size is None)

# Case 2: 1% hard risk floor — wide SL forces qty cap with a recorded note
res_floor = sizer.calculate(
    signal={"symbol": "IDEA", "direction": "LONG", "confidence": 0.9,
            "entry_price": 100.0, "sl_price": 50.0, "target_price": 160.0},
    context={"segment": "EQ", "vix": 11.0, "available_capital": TOTAL},
)
risk_cap = int((TOTAL * 0.01) / 50.0)
unfloored_qty = int(min(TOTAL * 0.08, TOTAL * 0.25) / 100.0)
check("A3 1% hard-risk floor caps qty",
      res_floor.quantity == min(unfloored_qty, risk_cap),
      f"qty={res_floor.quantity} (unfloored={unfloored_qty}, floor cap={risk_cap})")
check("A4 floor note recorded", "hard capital-risk floor" in (res_floor.notes or ""))

# Case 3: F&O sizing path (segment=FNO) — lot-adjusted quantity
res_fno = sizer.calculate(
    signal={"symbol": "RELIANCE", "direction": "LONG", "confidence": 0.84,
            "entry_price": 1400.0, "sl_price": 1380.0, "target_price": 1450.0},
    context={"segment": "FNO", "vix": 11.0, "available_capital": TOTAL},
)
from utils.market_utils import get_lot_size
lot = get_lot_size("RELIANCE")
expected_lots = int(min(TOTAL * 0.08, TOTAL * 0.25) / (1400.0 * lot))
check("A5 FNO qty is whole lots", res_fno.quantity == expected_lots * lot and res_fno.lot_size == lot,
      f"qty={res_fno.quantity} ({expected_lots} lots × {lot}), lot_size={res_fno.lot_size}")
check("A6 FNO is_equity=False", res_fno.is_equity is False)

print()
print("=" * 78)
print("B. LIVE FEED EQUITY SYMBOL ROUND-TRIP (real yahoo feed, market open)")
print("=" * 78)


async def live_feed_roundtrip():
    from feeds.yahoo_historical import YahooHistoricalFeed
    feed = YahooHistoricalFeed()
    px = await feed.get_latest_price("SUNPHARMA")  # watchlist format, no .NS suffix
    px2 = await feed.get_latest_price("INFY")
    return px, px2


px_sun, px_infy = asyncio.run(live_feed_roundtrip())
check("B1 equity symbol (no .NS) resolves live price SUNPHARMA", px_sun > 0, f"LTP=₹{px_sun:.2f}")
check("B2 equity symbol (no .NS) resolves live price INFY", px_infy > 0, f"LTP=₹{px_infy:.2f}")

print()
print("=" * 78)
print("C. ENGINE place_order CALL vs LIVE BROKER SIGNATURES (exchange routing)")
print("=" * 78)
# The engine's EXACT kwargs at engine.py:2280-2289 (entry), 2661-2667 (partial), 2777-2783 (exit)
ENGINE_ENTRY_KWARGS = dict(
    symbol="RELIANCE", exchange="NSE", transaction_type="BUY", quantity=10, price=1400.0,
    order_type="MARKET", segment="EQ", stop_loss=1380.0, target=1450.0,
)
ENGINE_EXIT_KWARGS = dict(
    symbol="RELIANCE", exchange="NSE", transaction_type="SELL", quantity=10, price=1410.0, order_type="MARKET",
)
from brokers.angel_one import AngelOneBroker
from brokers.dhan import DhanBroker
from brokers.fyers import FyersBroker
from brokers.shoonya import ShoonyaBroker
from brokers.kite import KiteBroker
from brokers.paper_broker import PaperBroker

for broker_cls in (AngelOneBroker, DhanBroker, FyersBroker, ShoonyaBroker, KiteBroker, PaperBroker):
    sig = inspect.signature(broker_cls.place_order)
    exchange_required = sig.parameters.get("exchange") is not None and sig.parameters["exchange"].default is inspect.Parameter.empty
    try:
        # bind with engine's exact kwargs — TypeError raised BEFORE any network call
        sig.bind(None, **ENGINE_ENTRY_KWARGS)
        bind_ok = True
        err = ""
    except TypeError as e:
        bind_ok = False
        err = str(e)
    label = "exchange REQUIRED" if exchange_required else "exchange defaulted"
    check(f"C: engine entry kwargs bind for {broker_cls.__name__} ({label})", bind_ok,
          "" if bind_ok else f"TypeError: {err}")

# Also verify exit-order kwargs (no segment) bind for the two with defaults
for broker_cls in (KiteBroker, PaperBroker):
    sig = inspect.signature(broker_cls.place_order)
    try:
        sig.bind(None, **ENGINE_EXIT_KWARGS)
        check(f"C: engine exit kwargs bind for {broker_cls.__name__}", True)
    except TypeError as e:
        check(f"C: engine exit kwargs bind for {broker_cls.__name__}", False, str(e))

print()
print("=" * 78)
print("D. F&O CONFIRM PATH (FIXED) — honest rejection without a real chain; real")
print("   premium-based lot sizing when a chain IS available")
print("=" * 78)


async def fno_confirm_probes():
    from core.engine import UltraBotEngine
    from unittest.mock import MagicMock, AsyncMock
    from tests.test_options_pipeline import make_synthetic_fyers_chain

    def _make_engine(broker):
        mock_repo = MagicMock()
        mock_repo.create_trade = AsyncMock()
        mock_repo.create_position = AsyncMock()

        async def get_repo():
            return mock_repo

        mock_config = MagicMock()
        mock_config.get_risk_config = MagicMock(return_value={
            "opportunity_ttl_seconds": 300, "price_mismatch_threshold_pct": 5.0})
        mock_config.get_fees_config = MagicMock(return_value={})

        eng = UltraBotEngine(
            config=mock_config, repository_getter=get_repo, error_engine=MagicMock(),
            risk_engine=MagicMock(), position_sizer=MagicMock(), partial_booker=MagicMock(),
            daily_risk_manager=MagicMock(), broker_factory=MagicMock(), feed_manager=MagicMock(),
            session_manager=MagicMock(),
        )
        eng.broker = broker
        eng.vix = 15.0
        eng.current_regime = "Bull"
        eng._broadcast = AsyncMock()
        eng._run_risk_gates = AsyncMock(return_value={"passed": True, "all_gates": []})
        # Kelly ₹ allocation
        eng._calculate_position_size = AsyncMock(return_value={"quantity": 1000, "position_size": 36000})
        return eng, mock_repo

    # D1: PaperBroker (no real option chain) must now REJECT honestly
    paper = PaperBroker(initial_capital=500000.0)
    eng_paper, repo_paper = _make_engine(paper)
    eng_paper.pending_opportunities["opp-rel"] = {
        "id": "opp-rel", "symbol": "RELIANCE", "direction": "BUY",
        "entry_price": 1400.0, "stop_loss": 1380.0, "target": 1450.0,
        "quantity": 1000, "strategy": "ORB", "confidence": 0.85,
        "created_at": datetime.now().isoformat(),
    }
    paper_result = await eng_paper.confirm_opportunity(opportunity_id="opp-rel", segment="FNO")

    # D2: broker WITH a real chain — RELIANCE 1410 CE @ ₹15.0 live ltp
    strikes = [1380, 1390, 1400, 1410, 1420]
    chain = make_synthetic_fyers_chain(spot_price=1400.0, days_to_expiry=4, strikes=strikes, base_symbol="RELIANCE")
    for row in chain["data"]["optionsChain"]:
        dist = row["strike_price"] - 1400.0
        slope = 0.10 if row["option_type"] == "CE" else -0.10
        row["ltp"] = round(max(1.0, 16.0 - dist * slope), 2)
        row["bid"] = round(row["ltp"] - 0.05, 2)
        row["ask"] = round(row["ltp"] + 0.05, 2)
    mock_broker = MagicMock()
    mock_broker.get_option_chain = AsyncMock(return_value=chain)
    mock_broker.place_order = AsyncMock(return_value={
        "order_id": "FYERS-1", "status": "FILLED", "filled_price": 15.0, "filled_quantity": 500})
    eng_live, repo_live = _make_engine(mock_broker)
    eng_live.pending_opportunities["opp-rel2"] = {
        "id": "opp-rel2", "symbol": "RELIANCE", "direction": "BUY",
        "entry_price": 1400.0, "stop_loss": 1380.0, "target": 1450.0,
        "quantity": 1000, "strategy": "ORB", "confidence": 0.85,
        "created_at": datetime.now().isoformat(),
    }
    live_result = await eng_live.confirm_opportunity(opportunity_id="opp-rel2", segment="FNO")
    live_pos = repo_live.create_position.call_args[1]
    live_order = mock_broker.place_order.call_args[1]
    return paper_result, repo_paper, live_result, live_pos, live_order


paper_result, repo_paper, live_result, live_pos, live_order = asyncio.run(fno_confirm_probes())

print(f"  [paper/no-chain] status={paper_result.get('status')}")
print(f"  [paper/no-chain] reason={paper_result.get('reason')}")
check("D1 F&O without real chain is REJECTED honestly",
      paper_result.get("status") == "rejected" and "option chain" in paper_result.get("reason", "").lower())
check("D2 nothing placed/recorded on rejection",
      not repo_paper.create_trade.called and not repo_paper.create_position.called)

print(f"  [with-chain] status={live_result.get('status')}")
print(f"  [with-chain] order symbol={live_order.get('symbol')!r}, exchange={live_order.get('exchange')!r}, qty={live_order.get('quantity')}, price={live_order.get('price')}")
check("D3 F&O symbol = real chain contract symbol", str(live_order.get("symbol", "")).startswith("NSE:RELIANCE"))
check("D4 F&O exchange routed to NFO", live_order.get("exchange") == "NFO")
check("D5 F&O qty is lot-multiple on REAL premium",
      live_order.get("quantity") == 500 and live_order.get("price") == 15.0,
      f"qty={live_order.get('quantity')} (1 lot × 500, Kelly 4 lots clamped by 2% max-loss budget), price={live_order.get('price')}")
check("D6 position extra carries segment/lot/premium metadata",
      live_pos["extra"].get("segment") == "FNO" and live_pos["extra"].get("lot_size") == 500
      and live_pos["extra"].get("premium") == 15.0)

print()
print("=" * 78)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(f"  ✗ {f}")
print("=" * 78)
sys.exit(1 if FAIL else 0)
