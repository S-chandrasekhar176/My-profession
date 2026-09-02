"""Position Sizer with dynamic Kelly-based sizing, tier adjustments, and hard capital-risk floor.

Pipeline Steps:
  1. Strategy Performance / Expected Win Rate / Confidence input -> Base Kelly fraction
     (clamped to [kelly_min_fraction, kelly_max_fraction], tightened to 8% max)
  2. Confidence tier multiplier (1.0, 0.8, 0.5)
  3. Volatility (VIX) tier multiplier (1.0, 0.85, 0.65, 0.4)
  4. Drawdown tier multiplier (1.0, 0.9, 0.7, 0.4)
  5. Capital availability cap (max_capital_usage_pct, max_per_position_pct)
  6. Convert to lot-adjusted quantity based on instrument type (F&O vs Equity)
  7. Hard Capital-Risk Floor: Cap quantity so that max loss ((entry - sl) x quantity)
     never exceeds hard_risk_pct (default 1.0%) of total capital
     (independent safety floor).
  8. Minimum position size check (>= min_position_size)
"""
from typing import Any, Dict, Optional, Tuple
import math

from models.risk_state import SizingResult
from utils.market_utils import get_lot_size, is_fno_stock
from core.capital_resolver import resolve_total_capital


class PositionSizer:
    """Dynamic Kelly-based position sizer with tier adjustments and a hard
    capital-risk floor (``hard_risk_pct`` of total capital, default 1.0%)."""

    def __init__(self, config: Dict[str, Any], capital_config: Dict[str, Any]):
        self.config = config or {}
        self.capital_config = capital_config or {}

        # Position sizing parameters (tightened Kelly cap default: 8% = 0.08)
        self.kelly_min: float = float(self.config.get("kelly_min_fraction", 0.02))
        self.kelly_max: float = float(self.config.get("kelly_max_fraction", 0.08))

        # NOTE (v0.4.3, audit claim #5): hard_risk_pct is now a PROPERTY that
        # reads the LIVE config dict on every use instead of a value cached
        # here at construction. `self.config` is the same dict object as
        # settings._raw_config["position_sizing"], so an API update (which
        # dual-writes risk + position_sizing) applies to the running sizer
        # immediately — previously the sizer kept its init-time value until
        # a backend restart while G17 (rebuilt per validate) picked up the
        # new value, letting the two consumers silently diverge mid-session.
        # Settings._enforce_hard_risk_sync() additionally keeps the two
        # config sections equal, making risk.hard_risk_pct canonical.

        # Capital parameters
        self.total_capital: float = resolve_total_capital(
            config={"capital": self.capital_config} if isinstance(self.capital_config, dict) else self.capital_config
        )
        self.max_capital_usage_pct: float = float(
            self.capital_config.get("max_capital_usage_pct", 90)
        )
        self.min_position_size: float = float(self.capital_config.get("min_position_size", 5000))
        self.max_per_position_pct: float = float(
            self.capital_config.get("max_per_position_pct", 25)
        )

        # Tier configs
        self.confidence_tiers: Dict[str, Dict] = self.config.get("confidence_tiers", {
            "high": {"min": 0.8, "multiplier": 1.0},
            "medium": {"min": 0.6, "multiplier": 0.8},
            "low": {"min": 0.4, "multiplier": 0.5},
        })
        self.volatility_tiers: Dict[str, Dict] = self.config.get("volatility_tiers", {
            "calm": {"max_vix": 14, "multiplier": 1.0},
            "normal": {"max_vix": 18, "multiplier": 0.85},
            "nervous": {"max_vix": 22, "multiplier": 0.65},
            "fearful": {"max_vix": 999, "multiplier": 0.4},
        })
        self.drawdown_tiers: Dict[str, Dict] = self.config.get("drawdown_tiers", {
            "profit": {"min_pct": 0, "multiplier": 1.0},
            "small_loss": {"min_pct": -1, "multiplier": 0.9},
            "mod_loss": {"min_pct": -2, "multiplier": 0.7},
            "big_loss": {"min_pct": -3, "multiplier": 0.4},
        })

    @property
    def hard_risk_pct(self) -> float:
        """Hard capital-risk floor (% of total capital), read LIVE from the
        position_sizing config dict on every access.

        v0.4.3 (audit claim #5): ``hard_risk_pct`` is defined in two config
        sections (``risk`` — read by G17CostPreCheck — and
        ``position_sizing`` — read here). Reading it live (a) picks up API
        updates without a backend restart, keeping this sizer in lockstep
        with G17 (whose gates are rebuilt from the live risk dict on every
        validate), and (b) together with Settings._enforce_hard_risk_sync()
        guarantees both consumers always evaluate against the same budget.
        Defaults to 1.0 when absent/unparseable (matching G17's default).
        """
        try:
            return float(self.config.get("hard_risk_pct", 1.0))
        except (TypeError, ValueError):
            return 1.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(
        self,
        signal: Any = None,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> SizingResult:
        """Run the full sizing pipeline and return a SizingResult."""
        ctx = context or {}
        merged_ctx = {**ctx, **kwargs}

        # Helper to extract values checking signal, context, and kwargs
        def _get_val(key: str, alt_keys: list = None, default: Any = None) -> Any:
            all_keys = [key] + (alt_keys or [])
            for k in all_keys:
                if k in merged_ctx and merged_ctx[k] is not None:
                    return merged_ctx[k]
            if signal is not None:
                if isinstance(signal, dict):
                    for k in all_keys:
                        if k in signal and signal[k] is not None:
                            return signal[k]
                else:
                    for k in all_keys:
                        if hasattr(signal, k) and getattr(signal, k) is not None:
                            return getattr(signal, k)
            return default

        symbol = str(_get_val("symbol", ["tradingsymbol", "ticker"], "NIFTY"))
        confidence = float(_get_val("confidence", ["score"], 0.5) or 0.5)
        entry_price = float(_get_val("entry_price", ["current_price", "price", "entry"], 0.0) or 0.0)
        sl_price = float(_get_val("sl_price", ["stop_loss", "sl", "initial_sl"], 0.0) or 0.0)
        vix = float(_get_val("vix", ["india_vix"], 15.0) or 15.0)
        current_drawdown_pct = float(_get_val("current_drawdown_pct", ["drawdown_pct"], 0.0) or 0.0)
        available_capital = float(_get_val("available_capital", ["capital", "margin_available"], self.total_capital) or self.total_capital)

        # Strategy performance statistics (if provided by strategy metadata / tracker)
        win_rate = _get_val("win_rate", ["expected_win_rate"], None)
        avg_win = _get_val("avg_win", ["expected_avg_win"], None)
        avg_loss = _get_val("avg_loss", ["expected_avg_loss"], None)
        risk_reward = _get_val("risk_reward", ["avg_rr", "expected_rr"], None)

        # 1. Base Kelly Fraction (tightened to [kelly_min, kelly_max])
        raw_fraction = self._compute_base_kelly(
            confidence=confidence,
            win_rate=float(win_rate) if win_rate is not None else None,
            avg_win=float(avg_win) if avg_win is not None else None,
            avg_loss=float(avg_loss) if avg_loss is not None else None,
            risk_reward=float(risk_reward) if risk_reward is not None else None,
        )
        raw_fraction = max(self.kelly_min, min(self.kelly_max, raw_fraction))

        # 2. Confidence Tier Multiplier
        conf_tier_name, conf_multiplier = self._confidence_tier(confidence)

        # 3. Volatility (VIX) Tier Multiplier
        vol_tier_name, vol_multiplier = self._volatility_tier(vix)

        # 4. Drawdown Tier Multiplier
        dd_tier_name, dd_multiplier = self._drawdown_tier(current_drawdown_pct)

        # Combined tier multiplier
        adjusted_fraction = raw_fraction * conf_multiplier * vol_multiplier * dd_multiplier

        # Check for strategy-mandated half-sizing (e.g. Trend Reversal Strategy)
        extra_details = _get_val("extra_details", [], {}) or {}
        half_size = _get_val("half_size", [], False) or (isinstance(extra_details, dict) and extra_details.get("half_size", False))
        if half_size:
            adjusted_fraction *= 0.5

        # 5. Capital Allocation Caps
        max_usable = self.total_capital * (self.max_capital_usage_pct / 100.0)
        actual_usable = min(available_capital, max_usable)
        position_size = self.total_capital * adjusted_fraction
        position_size = min(position_size, actual_usable)

        # Cap at max_per_position_pct (capital allocation limit)
        max_single = self.total_capital * (self.max_per_position_pct / 100.0)
        position_size = min(position_size, max_single)

        # 6. Instrument & Lot Size Detection
        segment = str(_get_val("segment", ["instrument_type"], "")).upper()
        explicit_is_fno = _get_val("is_fno", [], None)

        if explicit_is_fno is not None:
            is_fno = bool(explicit_is_fno)
        elif segment in ("FNO", "FUT", "OPT", "FUTURES", "OPTIONS"):
            is_fno = True
        elif segment in ("EQ", "CASH", "EQUITY"):
            is_fno = False
        else:
            is_fno = is_fno_stock(symbol)

        is_equity = not is_fno

        # Preliminary quantity from Kelly position size
        quantity, lot_size = self._to_quantity(symbol, position_size, entry_price, is_fno=is_fno)

        notes_parts: list = []

        # 7. HARD CAPITAL-RISK FLOOR (hard_risk_pct Max Capital Risk per Trade)
        # Position size must NEVER risk more than hard_risk_pct (default 1.0%)
        # of total capital based on |entry_price - sl_price| x quantity.
        risk_per_unit = abs(entry_price - sl_price) if entry_price > 0 and sl_price > 0 else 0.0
        max_allowed_risk_rupees = self.total_capital * (self.hard_risk_pct / 100.0)

        if risk_per_unit > 0 and entry_price > 0:
            max_qty_by_risk = int(max_allowed_risk_rupees / risk_per_unit)
            if is_fno and lot_size and lot_size > 0:
                # Lot-adjusted floor cap
                max_qty_by_risk = (max_qty_by_risk // lot_size) * lot_size
            
            if quantity > max_qty_by_risk:
                notes_parts.append(
                    f"Quantity capped from {quantity} to {max_qty_by_risk} by "
                    f"{self.hard_risk_pct:g}% hard capital-risk floor "
                    f"(Risk ₹{risk_per_unit * quantity:,.0f} -> ₹{risk_per_unit * max_qty_by_risk:,.0f} <= ₹{max_allowed_risk_rupees:,.0f})"
                )
                quantity = max(0, max_qty_by_risk)

        # 8. Minimum Position Size Check
        if entry_price > 0 and quantity > 0:
            position_size = entry_price * quantity
        else:
            position_size = 0.0

        if position_size < self.min_position_size and entry_price > 0 and quantity > 0:
            # Check if bumping to minimum position size would violate the hard risk floor
            min_qty, _ = self._to_quantity(symbol, self.min_position_size, entry_price, is_fno=is_fno)
            if risk_per_unit > 0 and (risk_per_unit * min_qty) > max_allowed_risk_rupees:
                notes_parts.append(
                    f"Position size ₹{position_size:,.0f} below minimum ₹{self.min_position_size:,.0f}, "
                    f"but minimum bump suppressed to protect the {self.hard_risk_pct:g}% hard risk floor"
                )
            else:
                notes_parts.append(
                    f"Position size (₹{position_size:,.0f}) below minimum (₹{self.min_position_size:,.0f}), set to minimum"
                )
                quantity = min_qty
                position_size = entry_price * quantity

        # Final Metrics
        position_size_pct = (position_size / self.total_capital * 100.0) if self.total_capital > 0 else 0.0
        risk_amount = risk_per_unit * quantity
        risk_pct = (risk_amount / self.total_capital * 100.0) if self.total_capital > 0 else 0.0

        return SizingResult(
            method="dynamic_kelly",
            raw_fraction=raw_fraction,
            adjusted_fraction=adjusted_fraction,
            confidence_multiplier=conf_multiplier,
            volatility_multiplier=vol_multiplier,
            drawdown_multiplier=dd_multiplier,
            capital_available=available_capital,
            position_size=round(position_size, 2),
            position_size_pct=round(position_size_pct, 2),
            quantity=quantity,
            lot_size=lot_size if is_fno else None,
            risk_amount=round(risk_amount, 2),
            risk_pct=round(risk_pct, 2),
            confidence_tier=conf_tier_name,
            volatility_tier=vol_tier_name,
            drawdown_tier=dd_tier_name,
            is_equity=is_equity,
            notes="; ".join(notes_parts) if notes_parts else None,
        )

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _compute_base_kelly(
        self,
        confidence: float,
        win_rate: Optional[float] = None,
        avg_win: Optional[float] = None,
        avg_loss: Optional[float] = None,
        risk_reward: Optional[float] = None,
    ) -> float:
        """Compute base Kelly fraction from performance stats or confidence score."""
        # Full Kelly with Half-Kelly scaling: f* = (W * R - (1 - W)) / R * 0.5
        if win_rate is not None:
            w = win_rate if win_rate <= 1.0 else win_rate / 100.0
            r = None
            if avg_win is not None and avg_loss is not None and avg_loss > 0:
                r = avg_win / avg_loss
            elif risk_reward is not None and risk_reward > 0:
                r = risk_reward

            if r is not None and r > 0:
                kelly = (w * r - (1.0 - w)) / r
                # Half-Kelly safety multiplier
                half_kelly = max(0.0, kelly * 0.5)
                if half_kelly > 0:
                    return half_kelly

        # Default confidence-based Kelly: f = confidence * 0.25 clamped to max 0.08
        return confidence * 0.25

    def _confidence_tier(self, confidence: float) -> Tuple[str, float]:
        """Return (tier_name, multiplier) for the given confidence."""
        best_name = "low"
        best_mult = 0.5
        best_min = -1.0
        for name, spec in self.confidence_tiers.items():
            if confidence >= spec["min"] and spec["min"] > best_min:
                best_min = spec["min"]
                best_name = name
                best_mult = spec["multiplier"]
        return best_name, best_mult

    def _volatility_tier(self, vix: float) -> Tuple[str, float]:
        """Return (tier_name, multiplier) for the given VIX level."""
        for name, spec in self.volatility_tiers.items():
            if vix <= spec["max_vix"]:
                return name, spec["multiplier"]
        last_name = list(self.volatility_tiers.keys())[-1]
        return last_name, self.volatility_tiers[last_name]["multiplier"]

    def _drawdown_tier(self, drawdown_pct: float) -> Tuple[str, float]:
        """Return (tier_name, multiplier) for the given drawdown.

        Tiers ordered from mildest to most severe loss:
          profit: >= 0 (multiplier 1.0)
          small_loss: >= -1.0% (multiplier 0.9)
          mod_loss: >= -2.0% (multiplier 0.7)
          big_loss: < -2.0% (multiplier 0.4)
        """
        if drawdown_pct >= 0:
            return "profit", self.drawdown_tiers.get("profit", {}).get("multiplier", 1.0)

        # Sort negative tiers by min_pct descending (-1, -2, -3)
        sorted_tiers = sorted(
            [(k, v) for k, v in self.drawdown_tiers.items() if k != "profit"],
            key=lambda item: item[1].get("min_pct", 0),
            reverse=True,
        )
        for name, spec in sorted_tiers:
            if drawdown_pct >= spec.get("min_pct", 0):
                return name, spec.get("multiplier", 1.0)

        # Fallback to most severe tier
        last_name = sorted_tiers[-1][0] if sorted_tiers else "big_loss"
        last_mult = sorted_tiers[-1][1].get("multiplier", 0.4) if sorted_tiers else 0.4
        return last_name, last_mult


    def _to_quantity(
        self, symbol: str, target_value: float, entry_price: float, is_fno: bool = False
    ) -> Tuple[int, Optional[int]]:
        """Convert a target rupee value to (quantity, lot_size)."""
        lot_size = get_lot_size(symbol)
        if entry_price <= 0:
            return 0, (lot_size if is_fno else None)

        if is_fno:
            lots = int(target_value / (entry_price * lot_size))
            return max(lots * lot_size, 0), lot_size
        else:
            qty = int(target_value / entry_price)
            return max(qty, 0), None
