from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from config.settings import Settings
from core.engine import UltraBotEngine

IST = ZoneInfo("Asia/Kolkata")

def test_build_opportunity_assigns_real_strategy_ttls():
    """Verify that _build_opportunity resolves 180s for ORB, 360s for MRF, 420s for VC

    and 300s fallback using the REAL defaults.yaml loaded Settings.
    """
    settings = Settings()
    engine = UltraBotEngine.__new__(UltraBotEngine)
    engine.config = settings
    engine.current_regime = "Sideways"
    engine.vix = 14.0
    engine.partial_booker = None
    
    dummy_sizing = {
        "quantity": 10,
        "position_size": 25000.0,
        "position_size_pct": 5.0,
        "risk_amount": 500.0,
        "sizing_method": "dynamic_kelly",
        "capital_required": 25000.0,
        "kelly_fraction": 0.05,
        "volatility_tier": "calm",
        "drawdown_tier": "profit",
        "confidence_tier": "high",
        "is_equity": True,
        "lot_size": None,
        "expiry_date": None,
        "strike": None,
        "option_type": None,
        "is_reduced_size": False,
    }
    
    # 1. ORB Opportunity
    orb_opp = engine._build_opportunity(
        signal={"strategy": "ORB", "symbol": "RELIANCE", "direction": "BUY", "entry_price": 2500.0, "stop_loss": 2450.0, "target": 2600.0, "confidence": 0.8},
        strategy_name="ORB",
        symbol="RELIANCE",
        current_price=2500.0,
        sizing=dummy_sizing,
        risk_result={"passed": True, "notes": "ok", "all_gates": []},
    )
    assert orb_opp["ttl_seconds"] == 180
    created_at_orb = datetime.fromisoformat(orb_opp["created_at"])
    expiry_at_orb = datetime.fromisoformat(orb_opp["expiry_at"])
    assert int((expiry_at_orb - created_at_orb).total_seconds()) == 180

    # 2. MRF Opportunity
    mrf_opp = engine._build_opportunity(
        signal={"strategy": "MRF", "symbol": "BPCL", "direction": "BUY", "entry_price": 320.0, "stop_loss": 315.0, "target": 330.0, "confidence": 0.84},
        strategy_name="MRF",
        symbol="BPCL",
        current_price=320.0,
        sizing=dummy_sizing,
        risk_result={"passed": True, "notes": "ok", "all_gates": []},
    )
    assert mrf_opp["ttl_seconds"] == 360
    created_at_mrf = datetime.fromisoformat(mrf_opp["created_at"])
    expiry_at_mrf = datetime.fromisoformat(mrf_opp["expiry_at"])
    assert int((expiry_at_mrf - created_at_mrf).total_seconds()) == 360

    # 3. VC Opportunity
    vc_opp = engine._build_opportunity(
        signal={"strategy": "VC", "symbol": "INFY", "direction": "BUY", "entry_price": 1800.0, "stop_loss": 1780.0, "target": 1850.0, "confidence": 0.75},
        strategy_name="VC",
        symbol="INFY",
        current_price=1800.0,
        sizing=dummy_sizing,
        risk_result={"passed": True, "notes": "ok", "all_gates": []},
    )
    assert vc_opp["ttl_seconds"] == 420
    created_at_vc = datetime.fromisoformat(vc_opp["created_at"])
    expiry_at_vc = datetime.fromisoformat(vc_opp["expiry_at"])
    assert int((expiry_at_vc - created_at_vc).total_seconds()) == 420

    # 4. Fallback unknown strategy
    other_opp = engine._build_opportunity(
        signal={"strategy": "UNKNOWN_STRAT", "symbol": "TCS", "direction": "BUY", "entry_price": 4000.0, "stop_loss": 3950.0, "target": 4100.0, "confidence": 0.7},
        strategy_name="UNKNOWN_STRAT",
        symbol="TCS",
        current_price=4000.0,
        sizing=dummy_sizing,
        risk_result={"passed": True, "notes": "ok", "all_gates": []},
    )
    assert other_opp["ttl_seconds"] == 300
    created_at_other = datetime.fromisoformat(other_opp["created_at"])
    expiry_at_other = datetime.fromisoformat(other_opp["expiry_at"])
    assert int((expiry_at_other - created_at_other).total_seconds()) == 300
