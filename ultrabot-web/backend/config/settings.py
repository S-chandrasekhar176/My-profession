import logging
import os
import yaml
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from typing import Any, Dict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from defaults.yaml with .env overrides."""

    # app
    app_name: str = "UltraBot Web"
    app_version: str = "1.0.0"
    app_secret_key: str = "change-me-in-production"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    # auth
    auth_username: str = "admin"
    auth_password_hash: str = ""

    # Store full nested config (not a Pydantic field — set in __init__)
    _raw_config: Dict[str, Any] = {}

    model_config = ConfigDict(
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._raw_config = {}
        self._load_yaml()

    def _load_yaml(self):
        """Load defaults.yaml and merge with defaults.local.yaml and env overrides."""
        self._yaml_path = Path(__file__).parent / "defaults.yaml"
        if self._yaml_path.exists():
            with open(self._yaml_path, "r") as f:
                self._raw_config = yaml.safe_load(f) or {}

        # Merge local overrides if defaults.local.yaml exists (gitignored for secrets)
        local_yaml = Path(__file__).parent / "defaults.local.yaml"
        if local_yaml.exists():
            try:
                with open(local_yaml, "r") as f:
                    local_config = yaml.safe_load(f) or {}
                    self._deep_merge(self._raw_config, local_config)
            except Exception:
                pass

        # Override with env vars
        self._apply_env_overrides()

        # hard_risk_pct single-source-of-truth enforcement (v0.4.3, audit
        # claim #5): risk.hard_risk_pct is canonical; position_sizing's copy
        # is kept in sync so G17's cost budget and the sizer's hard floor can
        # never silently diverge after a manual YAML edit.
        self._enforce_hard_risk_sync()

    def _enforce_hard_risk_sync(self) -> None:
        """Keep risk.hard_risk_pct and position_sizing.hard_risk_pct in sync.

        The key is historically defined in TWO config sections (both ship as
        1.0 in defaults.yaml — v0.4.6 restored the pristine value after a
        test-payload leak briefly shipped 1.5) and read by TWO consumers:
          * G17CostPreCheck  reads risk.hard_risk_pct          (cost budget)
          * PositionSizer     reads position_sizing.hard_risk_pct (qty floor)
        Nothing previously enforced agreement after a MANUAL yaml edit — the
        two consumers would silently diverge on what "the" risk budget is.
        The API route (PUT /api/risk/limits) already dual-writes; this guard
        covers the load/save paths and makes ``risk`` the canonical owner:

          * both present + different  → LOUD warning + position_sizing synced
                                        to the risk value (in memory only; the
                                        next save() persists it)
          * only one present          → backfilled into the other (user intent
                                        from either section is preserved)
          * both missing / unparseable→ left alone; both consumers default 1.0
        """
        risk_cfg = self._raw_config.get("risk") if isinstance(self._raw_config.get("risk"), dict) else None
        ps_cfg = (
            self._raw_config.get("position_sizing")
            if isinstance(self._raw_config.get("position_sizing"), dict)
            else None
        )
        if risk_cfg is None and ps_cfg is None:
            return

        risk_val = self._as_float(risk_cfg.get("hard_risk_pct")) if risk_cfg else None
        ps_val = self._as_float(ps_cfg.get("hard_risk_pct")) if ps_cfg else None

        if risk_cfg is not None and ps_cfg is not None and risk_val is not None and ps_val is not None:
            if abs(risk_val - ps_val) > 1e-9:
                logger.warning(
                    "CONFIG INCONSISTENCY: risk.hard_risk_pct=%s but "
                    "position_sizing.hard_risk_pct=%s — these MUST agree (G17 "
                    "cost budget vs sizer hard floor). The 'risk' section is "
                    "canonical: position_sizing has been synced to %s in memory "
                    "and will persist on the next config save.",
                    risk_val, ps_val, risk_val,
                )
                ps_cfg["hard_risk_pct"] = risk_val
            return

        if risk_val is not None and ps_cfg is not None and "hard_risk_pct" not in ps_cfg:
            # Backfill: user set only the canonical section.
            ps_cfg["hard_risk_pct"] = risk_val
            logger.info(
                "position_sizing.hard_risk_pct backfilled from risk.hard_risk_pct=%s",
                risk_val,
            )
        elif ps_val is not None and risk_cfg is not None and "hard_risk_pct" not in risk_cfg:
            # Backfill: user set only the legacy section — preserve intent by
            # completing the canonical section (both consumers stay aligned).
            risk_cfg["hard_risk_pct"] = ps_val
            logger.info(
                "risk.hard_risk_pct backfilled from position_sizing.hard_risk_pct=%s",
                ps_val,
            )

    @staticmethod
    def _as_float(value: Any) -> "float | None":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> None:
        """Recursively merge override dictionary into base dictionary."""
        for key, value in override.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _apply_env_overrides(self):
        """Apply environment variable overrides to both flat and nested config."""
        if os.getenv("SECRET_KEY"):
            self.app_secret_key = os.getenv("SECRET_KEY")
        if os.getenv("APP_PORT"):
            self.app_port = int(os.getenv("APP_PORT"))
        if os.getenv("APP_HOST"):
            self.app_host = os.getenv("APP_HOST")
        if os.getenv("APP_NAME"):
            self.app_name = os.getenv("APP_NAME")
        if os.getenv("ADMIN_USERNAME"):
            self.auth_username = os.getenv("ADMIN_USERNAME")
        if os.getenv("ADMIN_PASSWORD_HASH"):
            self.auth_password_hash = os.getenv("ADMIN_PASSWORD_HASH")

    def get(self, *keys, default=None) -> Any:
        """Get nested config value using dot-path: get('risk', 'max_open_positions')"""
        val = self._raw_config
        for key in keys:
            if isinstance(val, dict) and key in val:
                val = val[key]
            else:
                return default
        return val

    def get_risk_config(self) -> Dict[str, Any]:
        return self._raw_config.get("risk", {})

    def get_capital_config(self) -> Dict[str, Any]:
        return self._raw_config.get("capital", {})

    def get_broker_config(self, name: str) -> Dict[str, Any]:
        brokers = self._raw_config.get("brokers", {})
        return brokers.get(name, {})

    def get_strategy_activation(self, regime: str) -> Dict[str, Any]:
        activation = self._raw_config.get("strategy_activation", {})
        return activation.get(regime, {})

    def get_shadow_strategies(self) -> list:
        """Strategies running in shadow mode (signals recorded, never traded)."""
        raw = self._raw_config.get("strategy_shadow_mode", [])
        if isinstance(raw, list):
            return [str(s).upper() for s in raw if s]
        return []

    def get_partial_booking_config(self) -> Dict[str, Any]:
        return self._raw_config.get("partial_booking", {})

    def get_position_sizing_config(self) -> Dict[str, Any]:
        return self._raw_config.get("position_sizing", {})

    def get_fees_config(self) -> Dict[str, Any]:
        return self._raw_config.get("fees", {})

    def get_market_config(self) -> Dict[str, Any]:
        return self._raw_config.get("market", {})

    def get_engine_config(self) -> Dict[str, Any]:
        return self._raw_config.get("engine", {})

    def get_notifications_config(self) -> Dict[str, Any]:
        return self._raw_config.get("notifications", {})

    def get_regime_config(self) -> Dict[str, Any]:
        return self._raw_config.get("regime", {})

    def get_watchlist_config(self) -> Dict[str, Any]:
        return self._raw_config.get("watchlist", {})

    def save(self) -> bool:
        """Write current _raw_config back to defaults.yaml for persistence."""
        try:
            # Re-enforce the hard_risk_pct single source of truth right before
            # persisting, so a hand-edited divergence can never reach disk.
            self._enforce_hard_risk_sync()
            yaml_path = getattr(self, '_yaml_path', Path(__file__).parent / "defaults.yaml")
            with open(yaml_path, "w") as f:
                yaml.dump(self._raw_config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            return True
        except Exception:
            return False

    @property
    def secret_key(self) -> str:
        return self.app_secret_key


settings = Settings()
