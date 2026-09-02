"""EOD (End-of-Day) report generator.

Aggregates session trades, positions, risk events, and strategy performance
into a structured EOD summary dict suitable for display and Telegram.
"""
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from utils.formatters import format_currency, format_pct

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class EODReportGenerator:
    """Generate a comprehensive end-of-day report.

    Uses a Repository instance to pull trades, positions, risk events,
    and strategy performance for a given session/date.
    """

    def __init__(self, repository):
        self.repository = repository

    async def generate(self, session_id: str, report_date: Optional[date] = None) -> Dict[str, Any]:
        """Build a full EOD report.

        Args:
            session_id: Trading session ID.
            report_date: Date for the report. Defaults to today.

        Returns:
            Dict with keys: session_id, date, trades, positions, pnl_summary,
            strategy_breakdown, risk_events, capital_at_risk, open_positions_count,
            formatted_text.
        """
        if report_date is None:
            report_date = datetime.now(IST).date()

        date_str = report_date.isoformat()

        # Fetch data from repository
        trades = await self.repository.get_trades_by_date(date_str, limit=500)
        open_positions = await self.repository.get_open_positions()
        risk_events = await self.repository.get_todays_risk_events()
        strategies = await self.repository.get_all_strategy_performance()

        # Separate closed vs open trades
        closed_trades = [t for t in trades if t.status == "CLOSED"]
        open_trades = [t for t in trades if t.status == "OPEN"]

        # P&L calculations
        pnl_summary = self._compute_pnl_summary(closed_trades)

        # Strategy breakdown
        strategy_breakdown = self._compute_strategy_breakdown(closed_trades)

        # Sector breakdown
        sector_breakdown = self._compute_sector_breakdown(closed_trades)

        # Trade details list
        trade_details = self._format_trade_details(trades)

        # Capital at risk (sum of invested amounts in open positions)
        capital_at_risk = sum(
            getattr(p, "invested_amount", 0) or 0 for p in open_positions
        )

        report = {
            "session_id": session_id,
            "date": date_str,
            "generated_at": datetime.now(IST).isoformat(),
            "pnl_summary": pnl_summary,
            "strategy_breakdown": strategy_breakdown,
            "sector_breakdown": sector_breakdown,
            "risk_events_count": len(risk_events),
            "risk_events": [
                {
                    "event_type": getattr(r, "event_type", ""),
                    "severity": getattr(r, "severity", ""),
                    "message": getattr(r, "message", ""),
                    "created_at": getattr(r, "created_at", ""),
                }
                for r in risk_events[:20]
            ],
            "total_trades": len(trades),
            "closed_trades": len(closed_trades),
            "open_trades": len(open_trades),
            "open_positions_count": len(open_positions),
            "capital_at_risk": round(capital_at_risk, 2),
            "trade_details": trade_details,
            "strategies_tried": list(set(
                getattr(t, "strategy", "") for t in trades if getattr(t, "strategy", "")
            )),
        }

        # Build formatted text for Telegram / display
        report["formatted_text"] = self._build_formatted_text(report, pnl_summary, trade_details)

        return report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_pnl_summary(closed_trades: list) -> Dict[str, Any]:
        """Compute aggregate P&L from closed trades."""
        total = len(closed_trades)
        wins = 0
        losses = 0
        breakeven = 0
        gross_pnl = 0.0
        total_fees = 0.0
        net_pnl = 0.0
        total_invested = 0.0
        best_pnl = 0.0
        worst_pnl = 0.0
        avg_win = 0.0
        avg_loss = 0.0
        win_pnl_sum = 0.0
        loss_pnl_sum = 0.0

        for t in closed_trades:
            t_pnl = float(getattr(t, "net_pnl", 0))
            t_fees = float(getattr(t, "fees", 0)) + float(getattr(t, "brokerage", 0))
            t_gross = float(getattr(t, "pnl", t_pnl + t_fees))
            invested = float(getattr(t, "invested_amount", 0))

            gross_pnl += t_gross
            total_fees += t_fees
            net_pnl += t_pnl
            total_invested += invested

            if t_pnl > 0:
                wins += 1
                win_pnl_sum += t_pnl
            elif t_pnl < 0:
                losses += 1
                loss_pnl_sum += t_pnl
            else:
                breakeven += 1

            if t_pnl > best_pnl:
                best_pnl = t_pnl
            if t_pnl < worst_pnl:
                worst_pnl = t_pnl

        avg_win = win_pnl_sum / wins if wins > 0 else 0.0
        avg_loss = loss_pnl_sum / losses if losses > 0 else 0.0

        win_rate = (wins / total * 100) if total > 0 else 0.0
        profit_factor = abs(win_pnl_sum / loss_pnl_sum) if loss_pnl_sum != 0 else float('inf')
        roi = (net_pnl / total_invested * 100) if total_invested > 0 else 0.0

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate": round(win_rate, 1),
            "gross_pnl": round(gross_pnl, 2),
            "total_fees": round(total_fees, 2),
            "net_pnl": round(net_pnl, 2),
            "best_trade": round(best_pnl, 2),
            "worst_trade": round(worst_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else 0.0,
            "roi": round(roi, 2),
        }

    @staticmethod
    def _compute_strategy_breakdown(closed_trades: list) -> Dict[str, Dict[str, Any]]:
        """Per-strategy P&L breakdown."""
        by_strat: Dict[str, Dict[str, Any]] = {}

        for t in closed_trades:
            strat = getattr(t, "strategy", "Unknown") or "Unknown"
            t_pnl = float(getattr(t, "net_pnl", 0))
            t_fees = float(getattr(t, "fees", 0)) + float(getattr(t, "brokerage", 0))

            if strat not in by_strat:
                by_strat[strat] = {
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "net_pnl": 0.0,
                    "total_fees": 0.0,
                }

            by_strat[strat]["trades"] += 1
            by_strat[strat]["net_pnl"] += t_pnl
            by_strat[strat]["total_fees"] += t_fees

            if t_pnl > 0:
                by_strat[strat]["wins"] += 1
            elif t_pnl < 0:
                by_strat[strat]["losses"] += 1

        # Derived metrics
        for strat, s in by_strat.items():
            total = s["trades"]
            s["win_rate"] = round((s["wins"] / total * 100), 1) if total > 0 else 0.0
            s["net_pnl"] = round(s["net_pnl"], 2)
            s["total_fees"] = round(s["total_fees"], 2)

        return by_strat

    @staticmethod
    def _compute_sector_breakdown(closed_trades: list) -> Dict[str, float]:
        """Per-sector net P&L."""
        from utils.market_utils import get_stock_sector

        by_sector: Dict[str, float] = {}
        for t in closed_trades:
            sym = getattr(t, "symbol", "")
            sector = get_stock_sector(sym)
            t_pnl = float(getattr(t, "net_pnl", 0))
            by_sector[sector] = by_sector.get(sector, 0.0) + t_pnl

        return {k: round(v, 2) for k, v in sorted(by_sector.items(), key=lambda x: x[1], reverse=True)}

    @staticmethod
    def _format_trade_details(trades: list) -> List[Dict[str, Any]]:
        """Convert trade ORM objects to serialisable dicts."""
        details = []
        for t in trades:
            details.append({
                "id": getattr(t, "id", ""),
                "symbol": getattr(t, "symbol", ""),
                "direction": getattr(t, "direction", ""),
                "strategy": getattr(t, "strategy", ""),
                "entry_price": float(getattr(t, "entry_price", 0)),
                "exit_price": float(getattr(t, "exit_price", 0) or 0),
                "sl": float(getattr(t, "sl", 0)),
                "target": float(getattr(t, "target", 0)),
                "qty": int(getattr(t, "qty", 0)),
                "pnl": float(getattr(t, "pnl", 0)),
                "fees": float(getattr(t, "fees", 0)),
                "brokerage": float(getattr(t, "brokerage", 0)),
                "net_pnl": float(getattr(t, "net_pnl", 0)),
                "status": getattr(t, "status", ""),
                "entry_time": str(getattr(t, "entry_time", "")),
                "exit_time": str(getattr(t, "exit_time", "")),
            })
        return details

    @staticmethod
    def _build_formatted_text(report: Dict, pnl: Dict, trades: List[Dict]) -> str:
        """Build a plain-text formatted EOD summary."""
        pnl_emoji = "🟢" if pnl["net_pnl"] >= 0 else "🔴"
        lines = [
            f"EOD Report – {report['date']}",
            f"{'=' * 40}",
            f"",
            f"{pnl_emoji} Net P&L: {format_currency(pnl['net_pnl'], show_sign=True)}",
            f"   Gross: {format_currency(pnl['gross_pnl'], show_sign=True)}  |  Fees: {format_currency(pnl['total_fees'])}",
            f"",
            f"Trades: {report['total_trades']}  (W: {pnl['wins']}  L: {pnl['losses']}  BE: {pnl['breakeven']})",
            f"Win Rate: {pnl['win_rate']}%  |  Profit Factor: {pnl['profit_factor']}",
            f"ROI: {format_pct(pnl['roi'])}",
            f"Best: {format_currency(pnl['best_trade'], show_sign=True)}  |  Worst: {format_currency(pnl['worst_trade'], show_sign=True)}",
            f"Avg Win: {format_currency(pnl['avg_win'], show_sign=True)}  |  Avg Loss: {format_currency(pnl['avg_loss'])}",
            f"",
            f"Open Positions: {report['open_positions_count']}  |  Capital at Risk: {format_currency(report['capital_at_risk'])}",
            f"Risk Events: {report['risk_events_count']}",
            f"",
        ]

        # Strategy breakdown
        if report["strategy_breakdown"]:
            lines.append("Strategy Breakdown:")
            for strat, s in report["strategy_breakdown"].items():
                lines.append(
                    f"  {strat}: {s['trades']} trades, WR {s['win_rate']}%, P&L {format_currency(s['net_pnl'], show_sign=True)}"
                )
            lines.append("")

        # Top 10 trades
        if trades:
            lines.append("Trades:")
            for td in trades[:10]:
                sym = td.get("symbol", "?")
                d = td.get("direction", "")
                st = td.get("strategy", "")
                p = td.get("net_pnl", 0)
                status = td.get("status", "")
                lines.append(f"  {sym} {d} {st} → {format_currency(p, show_sign=True)} [{status}]")

        return "\n".join(lines)
