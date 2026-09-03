"""v0.4.8 P2 regression tests — G15 morning baseline + EOD PDF pipeline.

  * HF-6: the 20-bar volume baseline swallowed the opening-auction spike,
    so rel-volume in 09:15-09:50 read structurally low (live: 40 G15
    rejections at 0.21x-0.69x vs 1.00x). The spike-TRIMMED baseline must
    surface genuine relative-volume spikes in the morning window while
    leaving afternoon behaviour unchanged.
  * EOD PDF (Option A): reportlab render from an EODReportGenerator-shaped
    dict produces a valid PDF for the 15:35 scheduler job to archive and
    push to Telegram.
"""
from __future__ import annotations

import pandas as pd
import pytest

from notifications.eod_pdf import generate_eod_pdf
from notifications.eod_report import EODReportGenerator
from scanner.technical_scanner import TechnicalScanner, _VOLUME_ANOMALY_RATIO


def _df_with_opening_spike(spike=10000.0, normal=100.0, bars_after_spike=18):
    """1m bars: one auction-spike bar followed by normal-volume bars."""
    volumes = [spike] + [normal] * bars_after_spike
    volumes.append(normal)  # the "current" bar (replaced by caller)
    rows = {
        "open": [100.0] * len(volumes),
        "high": [101.0] * len(volumes),
        "low": [99.0] * len(volumes),
        "close": [100.5] * len(volumes),
        "volume": volumes,
    }
    return pd.DataFrame(rows)


class TestG15MorningBaselineHf6:
    def _scan(self, df, current_volume):
        df = df.copy()
        df.loc[df.index[-1], "volume"] = current_volume
        return TechnicalScanner._check_volume_anomaly(
            None, "TEST", df, ltp=100.5, idx=len(df) - 1
        )

    def test_morning_spike_no_longer_suppressed_by_auction_bar(self):
        """Live morning scenario: current volume 12x normal but the auction
        bar in the window dragged the mean so ratio read < 2.0 and G15
        rejected. Trimmed baseline must surface the genuine spike."""
        df = _df_with_opening_spike()
        result = self._scan(df, current_volume=1200.0)
        # Old baseline: (10000 + 18*100)/19 = 631.6 → ratio 1.90 → rejected.
        # New trimmed baseline: spike dropped → 100 → ratio 12.0.
        assert result is not None, "genuine morning spike still suppressed"
        assert result["details"]["volume_ratio"] >= _VOLUME_ANOMALY_RATIO
        assert result["details"]["avg_volume"] == pytest.approx(100.0, rel=0.15)

    def test_afternoon_behaviour_unchanged_without_spike(self):
        df = _df_with_opening_spike(spike=100.0, bars_after_spike=18)
        result = self._scan(df, current_volume=300.0)
        assert result is not None
        assert result["details"]["volume_ratio"] == pytest.approx(3.0, rel=0.1)

    def test_quiet_morning_still_blocked(self):
        """G15 must keep blocking genuinely low relative volume."""
        df = _df_with_opening_spike(spike=100.0)
        result = self._scan(df, current_volume=120.0)
        assert result is None  # 1.2x < 2.0x requirement


def _sample_report():
    trades = [
        {
            "id": "t1", "symbol": "AMBUJACEM", "direction": "BUY",
            "strategy": "SIC", "entry_price": 407.0, "exit_price": 406.15,
            "qty": 98, "pnl": -83.30, "fees": 95.97, "net_pnl": -179.27,
            "status": "CLOSED", "entry_time": "2026-09-01T10:30:58+05:30",
            "exit_time": "2026-09-01T11:01:00+05:30",
        },
        {
            "id": "t2", "symbol": "HCLTECH", "direction": "BUY",
            "strategy": "PTC", "entry_price": 1349.28, "exit_price": 1366.20,
            "qty": 22, "pnl": 462.82, "fees": 123.71, "net_pnl": 339.11,
            "status": "CLOSED", "entry_time": "2026-09-01T11:29:00+05:30",
            "exit_time": "2026-09-01T12:44:00+05:30",
        },
    ]
    return {
        "session_id": "sess-1",
        "date": "2026-09-01",
        "generated_at": "2026-09-01T15:35:00+05:30",
        "pnl_summary": {
            "total": 2, "wins": 1, "losses": 1, "breakeven": 0,
            "win_rate": 50.0, "gross_pnl": 379.52, "total_fees": 219.68,
            "net_pnl": 159.84, "best_trade": 339.11, "worst_trade": -179.27,
            "avg_win": 339.11, "avg_loss": -179.27, "profit_factor": 1.89,
            "roi": 0.03,
        },
        "strategy_breakdown": {
            "SIC": {"trades": 1, "wins": 0, "losses": 1,
                    "net_pnl": -179.27, "total_fees": 95.97, "win_rate": 0.0},
            "PTC": {"trades": 1, "wins": 1, "losses": 0,
                    "net_pnl": 339.11, "total_fees": 123.71, "win_rate": 100.0},
        },
        "risk_events_count": 3,
        "closed_trades": 2,
        "open_positions_count": 0,
        "capital_at_risk": 0.0,
        "trade_details": trades,
    }


class TestEodPdfGeneration:
    @pytest.fixture
    def sample_report(self):
        return _sample_report()

    def test_pdf_renders_and_is_valid(self, sample_report, tmp_path):
        out = tmp_path / "EOD_2026-09-01.pdf"
        result = generate_eod_pdf(sample_report, str(out))
        assert out.exists()
        assert result == str(out.resolve()) or result.endswith("EOD_2026-09-01.pdf")
        head = out.read_bytes()[:5]
        assert head == b"%PDF-"
        # non-trivial content (tables rendered)
        assert out.stat().st_size > 2000

    def test_pdf_handles_empty_day(self, tmp_path):
        empty = {
            "session_id": "", "date": "2026-09-02",
            "generated_at": "2026-09-02T15:35:00+05:30",
            "pnl_summary": {"total": 0, "wins": 0, "losses": 0,
                            "net_pnl": 0.0, "gross_pnl": 0.0, "total_fees": 0.0,
                            "win_rate": 0.0, "profit_factor": 0.0, "roi": 0.0,
                            "best_trade": 0.0, "worst_trade": 0.0,
                            "avg_win": 0.0, "avg_loss": 0.0, "breakeven": 0},
            "strategy_breakdown": {}, "risk_events_count": 0,
            "closed_trades": 0, "open_positions_count": 0,
            "capital_at_risk": 0.0, "trade_details": [],
        }
        out = tmp_path / "EOD_empty.pdf"
        generate_eod_pdf(empty, str(out))
        assert out.read_bytes()[:5] == b"%PDF-"

    def test_pdf_has_no_raw_rupee_glyph_issues(self, sample_report, tmp_path):
        """₹ is replaced by 'Rs.' because Helvetica lacks the glyph — the
        generator must not raise on currency formatting."""
        out = tmp_path / "EOD_glyph.pdf"
        generate_eod_pdf(sample_report, str(out))
        assert out.exists()


class TestEodPnlSummaryReconciliation:
    @pytest.mark.asyncio
    async def test_summary_sums_full_round_trip_values(self):
        """The report generator must simply AGGREGATE what the trades table
        holds — after HF-9 the trade rows already include partial legs."""

        class FakeTrade:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        trades = [
            FakeTrade(status="CLOSED", net_pnl=339.11, fees=123.71, pnl=462.82,
                      brokerage=0.0, invested_amount=29684.16, strategy="PTC",
                      symbol="HCLTECH", direction="BUY", exit_price=1366.20,
                      entry_price=1349.28, sl=0, target=0, qty=22,
                      entry_time="x", exit_time="y"),
            FakeTrade(status="CLOSED", net_pnl=-179.27, fees=95.97, pnl=-83.30,
                      brokerage=0.0, invested_amount=39886.0, strategy="SIC",
                      symbol="AMBUJACEM", direction="BUY", exit_price=406.15,
                      entry_price=407.0, sl=0, target=0, qty=98,
                      entry_time="x", exit_time="y"),
        ]

        class FakeRepo:
            async def get_trades_by_date(self, date_str, limit=500):
                return trades

            async def get_open_positions(self):
                return []

            async def get_todays_risk_events(self):
                return []

            async def get_all_strategy_performance(self):
                return []

        report = await EODReportGenerator(FakeRepo()).generate("sess-1")
        pnl = report["pnl_summary"]
        assert pnl["net_pnl"] == pytest.approx(159.84, abs=0.01)
        assert pnl["total_fees"] == pytest.approx(219.68, abs=0.01)
        assert pnl["gross_pnl"] == pytest.approx(379.52, abs=0.01)
        assert pnl["net_pnl"] == pytest.approx(pnl["gross_pnl"] - pnl["total_fees"], abs=0.02)
