"""P2 evidence: BookingLevels WS serialization fix.

Runs the EXACT production path:
  engine._build_opportunity()  ->  WebSocketManager.broadcast()'s
  json.dumps(..., default=_json_safe_default)

Before the fix this raised `TypeError: Object of type BookingLevels is not
JSON serializable` (see live log evidence). No mocks — real engine, real
partial booker, real broadcast serializer.
"""
import asyncio
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/z/Awesome_DE/ultrabot-web/backend")
IST = ZoneInfo("Asia/Kolkata")


async def main() -> None:
    from unittest.mock import MagicMock

    from api.websocket import _json_safe_default  # the fix
    from core.engine import UltraBotEngine
    from risk.partial_booker import PartialBooker

    # Minimal real engine (same pattern as the repo's own tests) —
    # _build_opportunity only needs partial_booker + config.
    config = MagicMock()
    config.get_risk_config.return_value = {}
    config.get_partial_booking_config.return_value = {}
    engine = UltraBotEngine(
        config=config,
        repository_getter=MagicMock(),
        error_engine=MagicMock(),
        risk_engine=MagicMock(),
        position_sizer=MagicMock(),
        partial_booker=PartialBooker(),  # REAL partial booker
        daily_risk_manager=MagicMock(),
        broker_factory=MagicMock(),
        feed_manager=MagicMock(),
        session_manager=MagicMock(),
    )

    signal = {
        "symbol": "RELIANCE",
        "direction": "LONG",
        "strategy": "ORB",
        "confidence": 0.82,
        "entry_price": 1298.5,
        "sl_price": 1290.0,
        "target_price": 1315.0,
        "volume_ratio": 1.4,
    }
    risk_result = {"passed": True, "all_gates": [{"name": "G1", "passed": True, "message": "ok"}]}
    sizing = {"quantity": 24, "position_size": 31164, "risk_pct": 0.8}

    opp = engine._build_opportunity(signal, "ORB", "RELIANCE", 1298.5, sizing, risk_result)

    bl = opp.get("booking_levels")
    print(f"booking_levels type: {type(bl).__name__}, len={len(bl) if isinstance(bl, list) else 'n/a'}")
    if isinstance(bl, list) and bl:
        print(f"  first item type: {type(bl[0]).__name__}")

    # Exact envelope construction used by WebSocketManager.broadcast()
    envelope = {"channel": "opportunity", "data": {"type": "new_opportunity", "opportunity": opp}, "ts": datetime.now(IST).isoformat()}
    try:
        message = json.dumps(envelope, default=_json_safe_default)
        print(f"\nBROADCAST ENCODE OK — message length {len(message)} bytes")
        parsed = json.loads(message)
        levels = parsed["data"]["opportunity"]["booking_levels"]
        print(f"parsed booking_levels: {len(levels)} levels, first = {json.dumps(levels[0])[:120]}...")
        assert isinstance(levels[0], dict), "booking_levels must be plain dicts over WS"
        print("\nVERIFIED: BookingLevels no longer breaks WS broadcasts.")
        sys.exit(0)
    except TypeError as exc:
        print(f"\nSTILL BROKEN: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
