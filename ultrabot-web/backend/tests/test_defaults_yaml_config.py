import pytest
from config.settings import Settings

def test_real_defaults_yaml_contains_strategy_ttl_configuration():
    """Verify that Settings loaded from the real defaults.yaml file on disk contains

    the exact strategy_ttl_seconds mapping and opportunity_ttl_seconds: 300.
    """
    settings = Settings()
    risk_cfg = settings.get_risk_config()
    
    assert risk_cfg is not None, "risk config block missing from defaults.yaml"
    assert risk_cfg.get("opportunity_ttl_seconds") == 300, (
        f"Expected opportunity_ttl_seconds == 300, got: {risk_cfg.get('opportunity_ttl_seconds')}"
    )
    
    strat_map = risk_cfg.get("strategy_ttl_seconds")
    assert isinstance(strat_map, dict), (
        f"strategy_ttl_seconds must be a dict in defaults.yaml, got: {type(strat_map)}"
    )
    
    expected_ttls = {
        "ORB": 180,
        "MB": 180,
        "TRS": 180,
        "MRF": 360,
        "PTC": 360,
        "SIC": 360,
        "VC": 420,
    }
    
    for strat, expected_val in expected_ttls.items():
        assert strat_map.get(strat) == expected_val, (
            f"Strategy {strat} expected TTL {expected_val}s, but got: {strat_map.get(strat)}"
        )
