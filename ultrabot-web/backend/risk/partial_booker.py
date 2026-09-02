"""Standard 4-Stage Profit Booking and Dynamic Trailing Stop-Loss Engine.

Lifecycle Specification:
  Stage 1 (Breakeven Lock): Price moves 0.5% in favor -> Move SL to Entry + brokerage cost (0% exited)
  Stage 2 (First Book):     Price moves 1.0% in favor -> Book 25% of position, SL to 0.7% profit, trail 0.5% from peak
  Stage 3 (Main Book):      Price moves 2.0% in favor -> Book another 30% of position, SL to 1.5% profit, trail 0.8% from peak
  Stage 4 (Runner Trail):   Price moves 3.0%+ in favor -> Hold remaining 45%, trail SL at 1.0% from peak

Total booked across stages 2+3+4 = 25% + 30% + 45% = 100%.
Trailing SL is ratchet-protected: it only moves forward with new peaks, never retreating.
"""
from typing import Any, Dict, List, Optional, Union
from types import SimpleNamespace
import json
from models.risk_state import BookingLevels, BookingResult
from utils.direction import is_long_direction


class PartialBooker:
    """Manages 4-stage profit booking levels and dynamic trailing stop-loss."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.enabled: bool = cfg.get("enabled", True)

        # Brokerage / slippage buffer for Stage 1 breakeven lock (default 0.05%)
        self.brokerage_buffer_pct: float = float(cfg.get("brokerage_buffer_pct", 0.05))

        # Stage 1: Breakeven Lock (+0.5% move in favor, 0% book)
        self.s1_trigger_pct: float = float(cfg.get("stage1_trigger_pct", 0.5))
        self.s1_book_pct: float = float(cfg.get("stage1_book_pct", 0.0))

        # Stage 2: First Book (+1.0% move in favor, 25% book, trail 0.5% from peak, floor 0.7%)
        self.s2_trigger_pct: float = float(cfg.get("stage2_trigger_pct", 1.0))
        self.s2_book_pct: float = float(cfg.get("stage2_book_pct", 25.0))
        self.s2_trail_pct: float = float(cfg.get("stage2_trail_pct", 0.5))
        self.s2_floor_profit_pct: float = float(cfg.get("stage2_floor_profit_pct", 0.7))

        # Stage 3: Main Book (+2.0% move in favor, 30% book, trail 0.8% from peak, floor 1.5%)
        self.s3_trigger_pct: float = float(cfg.get("stage3_trigger_pct", 2.0))
        self.s3_book_pct: float = float(cfg.get("stage3_book_pct", 30.0))
        self.s3_trail_pct: float = float(cfg.get("stage3_trail_pct", 0.8))
        self.s3_floor_profit_pct: float = float(cfg.get("stage3_floor_profit_pct", 1.5))

        # Stage 4: Runner Trail (+3.0%+ move in favor, hold 45%, trail 1.0% from peak)
        self.s4_trigger_pct: float = float(cfg.get("stage4_trigger_pct", 3.0))
        self.s4_book_pct: float = float(cfg.get("stage4_book_pct", 45.0))
        self.s4_trail_pct: float = float(cfg.get("stage4_trail_pct", 1.0))

        self.trailing_method: str = cfg.get("trailing_sl_method", "peak_trail")
        self.trailing_step_pct: float = float(cfg.get("trailing_step_pct", 0.5))

    def _normalize_position(
        self,
        position: Any = None,
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        target: float = 0.0,
        direction: str = "LONG",
        quantity: int = 100,
        **kwargs: Any,
    ) -> Any:
        if position is not None:
            return position
        return SimpleNamespace(
            entry_price=entry_price,
            sl_price=stop_loss,
            stop_loss=stop_loss,
            target=target,
            target_price=target,
            direction=direction,
            quantity=quantity,
            initial_quantity=quantity,
            stages_fired=[],
            peak_price=entry_price,
            extra={},
        )

    def _get_direction(self, position: Any) -> str:
        """Canonical LONG/SHORT for broker-facing consumers.

        v0.4.4: comparisons inside this module go through the shared
        ``utils.direction.is_long_direction`` helper instead of raw
        ``== "LONG"`` so the backend-wide static guard can be strict
        (positions may carry BUY/SELL from the strategies). The returned
        value stays "LONG"/"SHORT" for callers that need the legacy
        vocabulary (e.g. transaction-type routing).
        """
        raw = str(getattr(position, "direction", "LONG")).upper()
        if raw in ("SHORT", "SELL"):
            return "SHORT"
        return "LONG"

    def _extract_stages_and_peak(self, position: Any, current_price: float) -> tuple[List[int], float, int, float]:
        """Extract stages_fired, peak_price, initial_qty, and current_sl from position."""
        entry = float(getattr(position, "entry_price", 0) or 0)
        direction = self._get_direction(position)

        # 1. Initial quantity & current quantity
        qty = int(getattr(position, "quantity", 0) or 100)
        initial_qty = getattr(position, "initial_quantity", None)
        if initial_qty is None and hasattr(position, "extra"):
            extra = position.extra
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except Exception:
                    extra = {}
            if isinstance(extra, dict):
                initial_qty = extra.get("initial_quantity")
        if initial_qty is None:
            initial_qty = qty
        initial_qty = int(initial_qty)

        # 2. Stages fired
        stages_fired = getattr(position, "stages_fired", None)
        if stages_fired is None and hasattr(position, "extra"):
            extra = position.extra
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except Exception:
                    extra = {}
            if isinstance(extra, dict):
                stages_fired = extra.get("stages_fired")
        if stages_fired is None:
            stages_fired = []
        else:
            stages_fired = list(stages_fired)

        # 3. Peak price
        peak_price = getattr(position, "peak_price", None)
        if peak_price is None and hasattr(position, "extra"):
            extra = position.extra
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except Exception:
                    extra = {}
            if isinstance(extra, dict):
                peak_price = extra.get("peak_price")

        if peak_price is None or peak_price <= 0:
            peak_price = entry if entry > 0 else current_price

        # Update peak price monotonically
        if is_long_direction(direction):
            peak_price = max(float(peak_price), current_price)
        else:
            peak_price = min(float(peak_price), current_price)

        # 4. Current Stop-Loss
        current_sl = float(
            getattr(position, "stop_loss", 0)
            or getattr(position, "sl_price", 0)
            or getattr(position, "initial_sl", 0)
            or 0
        )

        return stages_fired, peak_price, initial_qty, current_sl

    def _sync_position_state(
        self,
        position: Any,
        stages_fired: List[int],
        peak_price: float,
        current_sl: float,
        initial_qty: int,
    ) -> None:
        """Persist state mutations back to position object and extra dict."""
        try:
            setattr(position, "stages_fired", stages_fired)
            setattr(position, "peak_price", peak_price)
            setattr(position, "initial_quantity", initial_qty)
            if hasattr(position, "stop_loss"):
                setattr(position, "stop_loss", current_sl)
            if hasattr(position, "sl_price"):
                setattr(position, "sl_price", current_sl)

            if hasattr(position, "extra"):
                extra = position.extra
                is_str = False
                if isinstance(extra, str):
                    is_str = True
                    try:
                        extra = json.loads(extra)
                    except Exception:
                        extra = {}
                if isinstance(extra, dict):
                    extra["stages_fired"] = stages_fired
                    extra["peak_price"] = peak_price
                    extra["initial_quantity"] = initial_qty
                    extra["stop_loss"] = current_sl
                    if is_str:
                        position.extra = json.dumps(extra)
                    else:
                        position.extra = extra
        except Exception:
            pass

    def calculate_booking_levels(self, position: Any) -> List[BookingLevels]:
        """Return the 4 standard booking levels for a position based on % move in favor."""
        entry = float(getattr(position, "entry_price", 0) or 0)
        direction = self._get_direction(position)

        if not is_long_direction(direction):
            s1_trigger = entry * (1.0 - self.s1_trigger_pct / 100.0)
            s2_trigger = entry * (1.0 - self.s2_trigger_pct / 100.0)
            s3_trigger = entry * (1.0 - self.s3_trigger_pct / 100.0)
            s4_trigger = entry * (1.0 - self.s4_trigger_pct / 100.0)
        else:
            s1_trigger = entry * (1.0 + self.s1_trigger_pct / 100.0)
            s2_trigger = entry * (1.0 + self.s2_trigger_pct / 100.0)
            s3_trigger = entry * (1.0 + self.s3_trigger_pct / 100.0)
            s4_trigger = entry * (1.0 + self.s4_trigger_pct / 100.0)

        levels: List[BookingLevels] = [
            BookingLevels(
                level=1,
                stage_name="Stage 1: Breakeven Lock",
                trigger_pct=self.s1_trigger_pct,
                book_pct=self.s1_book_pct,
                trigger_price=round(s1_trigger, 2),
                trail_pct=0.0,
            ),
            BookingLevels(
                level=2,
                stage_name="Stage 2: First Book",
                trigger_pct=self.s2_trigger_pct,
                book_pct=self.s2_book_pct,
                trigger_price=round(s2_trigger, 2),
                trail_pct=self.s2_trail_pct,
            ),
            BookingLevels(
                level=3,
                stage_name="Stage 3: Main Book",
                trigger_pct=self.s3_trigger_pct,
                book_pct=self.s3_book_pct,
                trigger_price=round(s3_trigger, 2),
                trail_pct=self.s3_trail_pct,
            ),
            BookingLevels(
                level=4,
                stage_name="Stage 4: Runner Trail",
                trigger_pct=self.s4_trigger_pct,
                book_pct=self.s4_book_pct,
                trigger_price=round(s4_trigger, 2),
                trail_pct=self.s4_trail_pct,
            ),
        ]
        return levels

    def get_booking_levels(
        self,
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        target: float = 0.0,
        direction: str = "LONG",
        config: Optional[Dict[str, Any]] = None,
        position: Any = None,
        **kwargs: Any,
    ) -> List[BookingLevels]:
        """Universal signature for obtaining 4-stage booking levels."""
        pos = self._normalize_position(
            position=position,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            direction=direction,
            **kwargs,
        )
        return self.calculate_booking_levels(pos)

    def check_and_book(self, position: Any, current_price: float) -> BookingResult:
        """Determine if a new 4-stage booking level triggers and compute trailing SL."""
        if not self.enabled:
            return BookingResult(
                enabled=False,
                current_level=0,
                levels=[],
                trailing_sl_active=False,
            )

        entry = float(getattr(position, "entry_price", 0) or 0)
        direction = self._get_direction(position)
        original_sl = float(
            getattr(position, "initial_sl", 0)
            or getattr(position, "sl_price", 0)
            or getattr(position, "stop_loss", 0)
            or 0
        )

        stages_fired, peak_price, initial_qty, current_sl = self._extract_stages_and_peak(
            position, current_price
        )

        # Calculate percentage move in favorable direction
        if entry > 0:
            if is_long_direction(direction):
                move_pct = (current_price - entry) / entry * 100.0
            else:
                move_pct = (entry - current_price) / entry * 100.0
        else:
            move_pct = 0.0

        levels = self.calculate_booking_levels(position)

        triggered_level: Optional[int] = None
        book_pct: float = 0.0
        book_qty: int = 0
        stage_name: Optional[str] = None

        # Check stages sequentially (each stage fires exactly once)
        if move_pct >= self.s1_trigger_pct and 1 not in stages_fired:
            triggered_level = 1
            stage_name = "Stage 1: Breakeven Lock"
            book_pct = self.s1_book_pct
            book_qty = 0
            stages_fired.append(1)

        elif move_pct >= self.s2_trigger_pct and 2 not in stages_fired:
            triggered_level = 2
            stage_name = "Stage 2: First Book"
            book_pct = self.s2_book_pct
            book_qty = int(round(initial_qty * (self.s2_book_pct / 100.0)))
            stages_fired.append(2)

        elif move_pct >= self.s3_trigger_pct and 3 not in stages_fired:
            triggered_level = 3
            stage_name = "Stage 3: Main Book"
            book_pct = self.s3_book_pct
            book_qty = int(round(initial_qty * (self.s3_book_pct / 100.0)))
            stages_fired.append(3)

        elif move_pct >= self.s4_trigger_pct and 4 not in stages_fired:
            triggered_level = 4
            stage_name = "Stage 4: Runner Trail"
            book_pct = 0.0  # Hold remaining 45% in trailing runner mode
            book_qty = 0
            stages_fired.append(4)

        # Mark booked levels in the levels list
        for lvl in levels:
            if lvl.level in stages_fired:
                lvl.booked = True

        current_level = max(stages_fired) if stages_fired else 0

        # Calculate Trailing / Locked SL based on highest stage fired and peak price
        trailing_sl_active = current_level >= 2
        computed_sl: float = original_sl

        if 4 in stages_fired:
            # Stage 4: Trail at 1.0% behind peak favorable price
            if is_long_direction(direction):
                computed_sl = peak_price * (1.0 - self.s4_trail_pct / 100.0)
            else:
                computed_sl = peak_price * (1.0 + self.s4_trail_pct / 100.0)

        elif 3 in stages_fired:
            # Stage 3: Trail at 0.8% behind peak favorable price with 1.5% profit floor
            if is_long_direction(direction):
                floor_sl = entry * (1.0 + self.s3_floor_profit_pct / 100.0)
                trail_sl = peak_price * (1.0 - self.s3_trail_pct / 100.0)
                computed_sl = max(floor_sl, trail_sl)
            else:
                floor_sl = entry * (1.0 - self.s3_floor_profit_pct / 100.0)
                trail_sl = peak_price * (1.0 + self.s3_trail_pct / 100.0)
                computed_sl = min(floor_sl, trail_sl)

        elif 2 in stages_fired:
            # Stage 2: Trail at 0.5% behind peak favorable price with 0.7% profit floor
            if is_long_direction(direction):
                floor_sl = entry * (1.0 + self.s2_floor_profit_pct / 100.0)
                trail_sl = peak_price * (1.0 - self.s2_trail_pct / 100.0)
                computed_sl = max(floor_sl, trail_sl)
            else:
                floor_sl = entry * (1.0 - self.s2_floor_profit_pct / 100.0)
                trail_sl = peak_price * (1.0 + self.s2_trail_pct / 100.0)
                computed_sl = min(floor_sl, trail_sl)

        elif 1 in stages_fired:
            # Stage 1: Move SL to entry + brokerage buffer
            buffer_mult = self.brokerage_buffer_pct / 100.0
            if is_long_direction(direction):
                computed_sl = entry * (1.0 + buffer_mult)
            else:
                computed_sl = entry * (1.0 - buffer_mult)

        else:
            computed_sl = original_sl

        # Ratchet Protection: SL must NEVER retreat
        if is_long_direction(direction):
            final_sl = max(original_sl, current_sl, computed_sl)
        else:
            final_sl = computed_sl
            if original_sl > 0:
                final_sl = min(original_sl, final_sl)
            if current_sl > 0:
                final_sl = min(current_sl, final_sl)

        final_sl = round(final_sl, 2)

        # Calculate current remaining quantity
        current_qty = int(getattr(position, "quantity", initial_qty) or initial_qty)
        remaining_qty = max(0, current_qty - book_qty) if triggered_level else current_qty

        # Persist updated state
        self._sync_position_state(
            position=position,
            stages_fired=stages_fired,
            peak_price=peak_price,
            current_sl=final_sl,
            initial_qty=initial_qty,
        )

        return BookingResult(
            enabled=True,
            current_level=current_level,
            levels=levels,
            trailing_sl_active=trailing_sl_active,
            current_trailing_sl=final_sl if current_level >= 1 else None,
            trailing_method=self.trailing_method,
            trailing_step_pct=self.trailing_step_pct,
            triggered_level=triggered_level,
            stage_name=stage_name,
            book_pct=book_pct if triggered_level else None,
            book_qty=book_qty if triggered_level else None,
            remaining_qty=remaining_qty,
            stages_fired=stages_fired,
            peak_price=round(peak_price, 2),
            move_pct=round(move_pct, 2),
        )

    def check_partial_booking(
        self,
        current_price: float,
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        target: float = 0.0,
        direction: str = "LONG",
        position: Any = None,
        **kwargs: Any,
    ) -> BookingResult:
        """Universal signature for checking partial booking triggers."""
        pos = self._normalize_position(
            position=position,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            direction=direction,
            **kwargs,
        )
        return self.check_and_book(pos, current_price)

    def calculate_trailing_sl(self, position: Any, current_price: float) -> float:
        """Calculate trailing Stop-Loss price for a position at current price."""
        result = self.check_and_book(position, current_price)
        if result.current_trailing_sl is not None:
            return result.current_trailing_sl
        return float(
            getattr(position, "stop_loss", 0)
            or getattr(position, "sl_price", 0)
            or getattr(position, "entry_price", 0)
            or 0
        )
