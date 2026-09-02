"""Comprehensive unit tests for the 4-Stage Profit Booking & Peak Trailing Stop Loss System."""
import pytest
from types import SimpleNamespace

from risk.partial_booker import PartialBooker


CONFIG = {
    "enabled": True,
    "brokerage_buffer_pct": 0.05,
    "stage1_trigger_pct": 0.5,
    "stage1_book_pct": 0.0,
    "stage2_trigger_pct": 1.0,
    "stage2_book_pct": 25.0,
    "stage2_trail_pct": 0.5,
    "stage2_floor_profit_pct": 0.7,
    "stage3_trigger_pct": 2.0,
    "stage3_book_pct": 30.0,
    "stage3_trail_pct": 0.8,
    "stage3_floor_profit_pct": 1.5,
    "stage4_trigger_pct": 3.0,
    "stage4_book_pct": 45.0,
    "stage4_trail_pct": 1.0,
}


def make_position(entry=100.0, sl=95.0, target=115.0, direction="LONG", quantity=100):
    return SimpleNamespace(
        entry_price=entry,
        sl_price=sl,
        stop_loss=sl,
        initial_sl=sl,
        target_price=target,
        direction=direction,
        quantity=quantity,
        initial_quantity=quantity,
        stages_fired=[],
        peak_price=entry,
        extra={},
    )


@pytest.fixture
def booker():
    return PartialBooker(CONFIG)


class TestBookingLevels:
    def test_four_levels_long(self, booker):
        """Entry=100, LONG:
        Stage 1 @ 0.5% = 100.50 (Book 0%)
        Stage 2 @ 1.0% = 101.00 (Book 25%)
        Stage 3 @ 2.0% = 102.00 (Book 30%)
        Stage 4 @ 3.0% = 103.00 (Hold 45%)
        """
        pos = make_position(entry=100.0, sl=95.0)
        levels = booker.calculate_booking_levels(pos)
        assert len(levels) == 4
        assert levels[0].level == 1
        assert levels[0].trigger_price == 100.50
        assert levels[0].book_pct == 0.0

        assert levels[1].level == 2
        assert levels[1].trigger_price == 101.00
        assert levels[1].book_pct == 25.0

        assert levels[2].level == 3
        assert levels[2].trigger_price == 102.00
        assert levels[2].book_pct == 30.0

        assert levels[3].level == 4
        assert levels[3].trigger_price == 103.00
        assert levels[3].book_pct == 45.0

    def test_four_levels_short(self, booker):
        """Entry=200, SHORT:
        Stage 1 @ 0.5% = 199.00
        Stage 2 @ 1.0% = 198.00
        Stage 3 @ 2.0% = 196.00
        Stage 4 @ 3.0% = 194.00
        """
        pos = make_position(entry=200.0, sl=210.0, direction="SHORT")
        levels = booker.calculate_booking_levels(pos)
        assert len(levels) == 4
        assert levels[0].trigger_price == 199.00
        assert levels[1].trigger_price == 198.00
        assert levels[2].trigger_price == 196.00
        assert levels[3].trigger_price == 194.00


class TestFourStageLifecycleLong:
    def test_progressive_stages_and_ratchet_trailing(self, booker):
        """Simulate a trade stepping from entry through all stages and pullbacks:
        100.0 -> 100.2 -> 100.5 -> 101.0 -> 102.0 -> 103.5 -> 103.0 -> 105.0
        """
        pos = make_position(entry=100.0, sl=95.0, quantity=100)

        # Step 1: Sub-threshold movement (100.20, +0.2%)
        r0 = booker.check_and_book(pos, current_price=100.20)
        assert r0.triggered_level is None
        assert r0.current_level == 0
        assert r0.trailing_sl_active is False
        assert pos.stop_loss == 95.0
        assert pos.stages_fired == []

        # Step 2: Stage 1 Trigger (+0.5% at 100.50) -> Breakeven Lock
        r1 = booker.check_and_book(pos, current_price=100.50)
        assert r1.triggered_level == 1
        assert r1.current_level == 1
        assert r1.book_qty == 0
        assert r1.remaining_qty == 100
        assert pos.stages_fired == [1]
        assert r1.current_trailing_sl == 100.05  # entry + 0.05% buffer
        assert pos.stop_loss == 100.05

        # Re-check at same price: Stage 1 does NOT double-fire
        r1_dup = booker.check_and_book(pos, current_price=100.50)
        assert r1_dup.triggered_level is None
        assert r1_dup.current_level == 1
        assert pos.stages_fired == [1]

        # Step 3: Stage 2 Trigger (+1.0% at 101.00) -> Book 25%, SL to 0.7% floor (100.70)
        r2 = booker.check_and_book(pos, current_price=101.00)
        assert r2.triggered_level == 2
        assert r2.current_level == 2
        assert r2.book_qty == 25
        assert r2.remaining_qty == 75
        pos.quantity = 75  # Simulating engine quantity reduction
        assert pos.stages_fired == [1, 2]
        assert r2.trailing_sl_active is True
        # Floor SL = 100 * 1.007 = 100.70; Peak trail = 101 * 0.995 = 100.495 -> max is 100.70
        assert r2.current_trailing_sl == 100.70
        assert pos.stop_loss == 100.70

        # Step 4: Stage 3 Trigger (+2.0% at 102.00) -> Book 30% of orig (30), SL to 1.5% floor (101.50)
        r3 = booker.check_and_book(pos, current_price=102.00)
        assert r3.triggered_level == 3
        assert r3.current_level == 3
        assert r3.book_qty == 30
        assert r3.remaining_qty == 45
        pos.quantity = 45  # Simulating engine quantity reduction
        assert pos.stages_fired == [1, 2, 3]
        # Floor SL = 100 * 1.015 = 101.50; Peak trail = 102 * 0.992 = 101.184 -> max is 101.50
        assert r3.current_trailing_sl == 101.50
        assert pos.stop_loss == 101.50

        # Step 5: Stage 4 Trigger (+3.5% at 103.50) -> Hold 45% runner, Trail 1.0% from peak
        r4 = booker.check_and_book(pos, current_price=103.50)
        assert r4.triggered_level == 4
        assert r4.current_level == 4
        assert r4.book_qty == 0
        assert r4.remaining_qty == 45
        assert pos.stages_fired == [1, 2, 3, 4]
        # Peak trail = 103.50 * (1 - 0.01) = 102.465 -> round to 102.47
        assert r4.current_trailing_sl == 102.47
        assert pos.stop_loss == 102.47

        # Step 6: Pullback to 103.00 -> Confirm SL does NOT retreat
        r5 = booker.check_and_book(pos, current_price=103.00)
        assert r5.triggered_level is None
        assert r5.current_level == 4
        assert r5.current_trailing_sl == 102.47
        assert pos.stop_loss == 102.47  # SL remains ratcheted

        # Step 7: Push to new peak 105.00 -> SL trails forward
        r6 = booker.check_and_book(pos, current_price=105.00)
        assert r6.triggered_level is None
        assert r6.current_level == 4
        # Peak trail = 105.00 * 0.99 = 103.95
        assert r6.current_trailing_sl == 103.95
        assert pos.stop_loss == 103.95


class TestFourStageLifecycleShort:
    def test_short_stages_and_trailing(self, booker):
        """Short trade: Entry=200, SL=210, Qty=100.
        200 -> 199.0 (-0.5%) -> 198.0 (-1.0%) -> 196.0 (-2.0%) -> 193.0 (-3.5%) -> 194.0 (pullback)
        """
        pos = make_position(entry=200.0, sl=210.0, direction="SHORT", quantity=100)

        # Stage 1: 199.00
        r1 = booker.check_and_book(pos, current_price=199.00)
        assert r1.triggered_level == 1
        assert r1.current_trailing_sl == 199.90  # 200 * (1 - 0.0005)
        assert pos.stop_loss == 199.90

        # Stage 2: 198.00 -> Book 25
        r2 = booker.check_and_book(pos, current_price=198.00)
        assert r2.triggered_level == 2
        assert r2.book_qty == 25
        pos.quantity = 75
        # Floor SL = 200 * (1 - 0.007) = 198.60; Peak trail = 198 * 1.005 = 198.99 -> min is 198.60
        assert r2.current_trailing_sl == 198.60
        assert pos.stop_loss == 198.60

        # Stage 3: 196.00 -> Book 30
        r3 = booker.check_and_book(pos, current_price=196.00)
        assert r3.triggered_level == 3
        assert r3.book_qty == 30
        pos.quantity = 45
        # Floor SL = 200 * (1 - 0.015) = 197.00; Peak trail = 196 * 1.008 = 197.568 -> min is 197.00
        assert r3.current_trailing_sl == 197.00
        assert pos.stop_loss == 197.00

        # Stage 4: 193.00 -> Trail 1.0% from peak
        r4 = booker.check_and_book(pos, current_price=193.00)
        assert r4.triggered_level == 4
        # Peak trail = 193.00 * 1.01 = 194.93
        assert r4.current_trailing_sl == 194.93
        assert pos.stop_loss == 194.93

        # Pullback to 194.00 -> SL does not move upward (worse)
        r5 = booker.check_and_book(pos, current_price=194.00)
        assert r5.current_trailing_sl == 194.93
        assert pos.stop_loss == 194.93


class TestEdgeCases:
    def test_reversal_before_stage1(self, booker):
        """Simulate immediate adverse movement: Entry=100, SL=95.
        Price drops to 98.0 then 95.0. No booking triggers, SL unchanged.
        """
        pos = make_position(entry=100.0, sl=95.0)
        r = booker.check_and_book(pos, current_price=98.0)
        assert r.triggered_level is None
        assert r.current_level == 0
        assert pos.stop_loss == 95.0
        assert pos.stages_fired == []

        r2 = booker.check_and_book(pos, current_price=95.0)
        assert r2.triggered_level is None
        assert r2.current_level == 0
        assert pos.stop_loss == 95.0

    def test_total_lifecycle_quantity_accounting(self, booker):
        """Confirm total booked across the lifecycle sums to 100% (25% + 30% + 45%)."""
        initial_qty = 200
        pos = make_position(entry=100.0, sl=95.0, quantity=initial_qty)

        # Stage 1: 0% booked
        r1 = booker.check_and_book(pos, current_price=100.5)
        booked_s1 = r1.book_qty or 0
        assert booked_s1 == 0

        # Stage 2: 25% booked = 50
        r2 = booker.check_and_book(pos, current_price=101.0)
        booked_s2 = r2.book_qty or 0
        assert booked_s2 == 50
        pos.quantity = r2.remaining_qty

        # Stage 3: 30% booked = 60
        r3 = booker.check_and_book(pos, current_price=102.0)
        booked_s3 = r3.book_qty or 0
        assert booked_s3 == 60
        pos.quantity = r3.remaining_qty

        # Stage 4: runner active, remaining 45% = 90
        r4 = booker.check_and_book(pos, current_price=103.5)
        assert pos.quantity == 90
        runner_qty = pos.quantity

        total_booked = booked_s1 + booked_s2 + booked_s3 + runner_qty
        assert total_booked == initial_qty
        assert booked_s2 / initial_qty == 0.25
        assert booked_s3 / initial_qty == 0.30
        assert runner_qty / initial_qty == 0.45

    def test_disabled_booker(self):
        disabled_booker = PartialBooker({"enabled": False})
        pos = make_position(entry=100.0, sl=95.0)
        res = disabled_booker.check_and_book(pos, current_price=110.0)
        assert res.enabled is False
        assert res.current_level == 0
        assert res.trailing_sl_active is False
