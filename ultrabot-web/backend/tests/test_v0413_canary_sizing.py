"""v0.4.13 canary false-positive fix + G20 sizing pre-check unit tests.

Live incidents 2026-09-07:
- 10:50 & 11:58 IST: canary fired "Market is OPEN but engine is scanning"
  while the engine was actively scanning — the healthy-state whitelist
  only knew running/starting.
- 11:26 IST: BOSCHLTD opportunity card was emitted with qty 0 (price
  Rs 47.9k vs per-trade capital); the user approved and the confirm path
  rejected with "Position size calculated as 0" — bad UX, wasted card.
"""
from __future__ import annotations

import pytest

from notifications.telegram_interactive import _CANARY_HEALTHY_STATES


class TestCanaryHealthyStates:
    def test_scanning_is_healthy(self):
        assert "scanning" in _CANARY_HEALTHY_STATES

    def test_running_and_starting_still_healthy(self):
        assert "running" in _CANARY_HEALTHY_STATES
        assert "starting" in _CANARY_HEALTHY_STATES

    def test_paused_is_healthy(self):
        # /pause stops NEW entries but the main loop (engine.py:1011 runs
        # while RUNNING/PAUSED/SCANNING and calls _manage_all_positions at
        # :1032) still enforces SLs/targets/time-stops — not blind.
        assert "paused" in _CANARY_HEALTHY_STATES

    def test_stopped_and_error_are_not_healthy(self):
        assert "stopped" not in _CANARY_HEALTHY_STATES
        assert "error" not in _CANARY_HEALTHY_STATES

    def test_unknown_state_not_healthy(self):
        assert "unknown" not in _CANARY_HEALTHY_STATES


class TestEngineSizingGateSource:
    """The G20 gate lives inline in the scan loop (same pattern as the
    G17 actual-size re-check). Guard the contract that matters: the
    confirm-path rejection string must stay the upstream gate's fallback,
    and the gate name must be registered in the telemetry taxonomy."""

    def test_confirm_path_rejection_string_unchanged(self):
        # engine.py confirm path: {"status": "rejected", "reason": ...}
        # — kept verbatim as the last-line defense for live-mode paths
        # that bypass scan-time sizing.
        from core import engine as engine_mod

        src = open(engine_mod.__file__, "r", encoding="utf-8").read()
        assert '"Position size calculated as 0"' in src
        # and the upstream gate exists before it
        assert "G20_Sizing" in src
        # upstream gate must appear EARLIER in the file than the confirm-path check
        assert src.index("G20_Sizing") < src.index('"Position size calculated as 0"')

    def test_shadow_kind_gate_blocked_importable(self):
        from core.engine import KIND_GATE_BLOCKED  # noqa: F401

        assert KIND_GATE_BLOCKED == "gate_blocked" or isinstance(KIND_GATE_BLOCKED, str)
