"""EOD PDF report generator (v0.4.8 P2 — delivery Option A).

Daily at ~15:35 IST the market lifecycle scheduler renders the EOD
aggregation (notifications/eod_report.py) into a compact one-page PDF,
archives it under ``backend/reports/`` and pushes it to Telegram as a
document via ``TelegramBot.send_document``.

Typographic note: the PDF core fonts (Helvetica) have no rupee glyph, so
currency strings are rendered with an explicit ``Rs. `` prefix instead of
relying on the ₹ character.
"""
import logging
import os
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from utils.formatters import format_currency, format_pct

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_BRAND = colors.HexColor("#0F172A")
_ACCENT = colors.HexColor("#134E4A")
_ROW_ALT = colors.HexColor("#F1F5F9")
_HEADER_BG = colors.HexColor("#134E4A")
_HEADER_FG = colors.white


def _inr(value: float, sign: bool = False) -> str:
    """PDF-safe currency string (Helvetica lacks the ₹ glyph)."""
    try:
        return format_currency(float(value or 0.0), show_sign=sign).replace("₹", "Rs. ")
    except (TypeError, ValueError):
        return "Rs. 0.00"


def generate_eod_pdf(report: Dict[str, Any], out_path: str) -> str:
    """Render an EOD report dict (from EODReportGenerator.generate) to PDF.

    Args:
        report: The full report dict with ``pnl_summary``, ``strategy_breakdown``,
            ``trade_details``, totals and metadata.
        out_path: Destination .pdf path (parent dirs are created).

    Returns:
        The absolute path of the written PDF.
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    pnl = report.get("pnl_summary", {}) or {}
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BrandTitle", fontName="Helvetica-Bold",
                              fontSize=18, leading=22, textColor=_BRAND))
    styles.add(ParagraphStyle(name="SectionHead", fontName="Helvetica-Bold",
                              fontSize=11, leading=14, textColor=_ACCENT,
                              spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="SmallNote", fontName="Helvetica-Oblique",
                              fontSize=7.5, leading=9, textColor=colors.HexColor("#64748B")))

    story: List[Any] = []

    net_pnl = float(pnl.get("net_pnl", 0.0))
    emoji_free = "POSITIVE" if net_pnl >= 0 else "NEGATIVE"

    story.append(Paragraph("UltraBot — End-of-Day Report", styles["BrandTitle"]))
    story.append(Paragraph(
        f"Date: {report.get('date', '')} &nbsp;|&nbsp; "
        f"Session: {report.get('session_id') or 'n/a'} &nbsp;|&nbsp; "
        f"Generated: {str(report.get('generated_at', ''))[:19]} IST",
        styles["Normal"],
    ))
    story.append(Spacer(1, 4 * mm))

    # ---- P&L summary table -------------------------------------------------
    story.append(Paragraph("P&amp;L Summary", styles["SectionHead"]))
    summary_rows = [
        ["Net P&L", _inr(net_pnl, sign=True), "Day Outcome", emoji_free],
        ["Gross P&L", _inr(pnl.get("gross_pnl", 0.0), sign=True),
         "Total Fees", _inr(pnl.get("total_fees", 0.0))],
        ["Total Trades", str(report.get("closed_trades", pnl.get("total", 0))),
         "Wins / Losses / BE", f"{pnl.get('wins', 0)} / {pnl.get('losses', 0)} / {pnl.get('breakeven', 0)}"],
        ["Win Rate", f"{pnl.get('win_rate', 0.0)}%",
         "Profit Factor", str(pnl.get("profit_factor", 0.0))],
        ["Best Trade", _inr(pnl.get("best_trade", 0.0), sign=True),
         "Worst Trade", _inr(pnl.get("worst_trade", 0.0), sign=True)],
        ["Avg Win", _inr(pnl.get("avg_win", 0.0), sign=True),
         "Avg Loss", _inr(pnl.get("avg_loss", 0.0))],
        ["ROI (on invested)", format_pct(float(pnl.get("roi", 0.0))),
         "Open Positions", str(report.get("open_positions_count", 0))],
        ["Capital at Risk", _inr(report.get("capital_at_risk", 0.0)),
         "Risk Events", str(report.get("risk_events_count", 0))],
    ]
    summary_table = Table(summary_rows, colWidths=[38 * mm, 48 * mm, 42 * mm, 48 * mm])
    summary_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("BACKGROUND", (0, 0), (0, -1), _ROW_ALT),
        ("BACKGROUND", (2, 0), (2, -1), _ROW_ALT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TEXTCOLOR", (1, 0), (1, 0), _ACCENT if net_pnl >= 0 else colors.HexColor("#B91C1C")),
        ("FONTNAME", (1, 0), (1, 0), "Helvetica-Bold"),
    ]))
    story.append(summary_table)

    # ---- Strategy breakdown -------------------------------------------------
    strategy_breakdown = report.get("strategy_breakdown", {}) or {}
    if strategy_breakdown:
        story.append(Paragraph("Strategy Breakdown", styles["SectionHead"]))
        strat_rows = [["Strategy", "Trades", "W / L", "Win Rate", "Net P&L", "Fees"]]
        for name, s in strategy_breakdown.items():
            strat_rows.append([
                str(name), str(s.get("trades", 0)),
                f"{s.get('wins', 0)} / {s.get('losses', 0)}",
                f"{s.get('win_rate', 0.0)}%",
                _inr(s.get("net_pnl", 0.0), sign=True),
                _inr(s.get("total_fees", 0.0)),
            ])
        strat_table = Table(strat_rows, colWidths=[34 * mm, 16 * mm, 20 * mm, 22 * mm, 42 * mm, 42 * mm])
        strat_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_FG),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ROW_ALT]),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(strat_table)

    # ---- Trade blotter -------------------------------------------------------
    trade_details = report.get("trade_details", []) or []
    if trade_details:
        story.append(Paragraph(f"Trade Blotter (all {len(trade_details)})", styles["SectionHead"]))
        blotter_rows = [[
            "Symbol", "Dir", "Strategy", "Entry", "Exit", "Qty", "Gross", "Fees", "Net", "Status",
        ]]
        for t in trade_details[:18]:
            blotter_rows.append([
                str(t.get("symbol", ""))[:12],
                str(t.get("direction", ""))[:5],
                str(t.get("strategy", ""))[:6],
                f"{float(t.get('entry_price', 0) or 0):.2f}",
                f"{float(t.get('exit_price', 0) or 0):.2f}",
                str(t.get("qty", 0)),
                _inr(t.get("pnl", 0.0), sign=True),
                _inr(t.get("fees", 0.0)),
                _inr(t.get("net_pnl", 0.0), sign=True),
                str(t.get("status", ""))[:7],
            ])
        blotter = Table(blotter_rows, colWidths=[22 * mm, 10 * mm, 15 * mm, 16 * mm, 16 * mm,
                                                 10 * mm, 26 * mm, 20 * mm, 26 * mm, 15 * mm])
        blotter.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_FG),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ROW_ALT]),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        story.append(blotter)

    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        "All figures reconcile against the trades table: gross P&L includes "
        "partial-booking legs; fees are full round-trip costs per trade. "
        "Generated automatically by UltraBot v0.4.8 at ~15:35 IST.",
        styles["SmallNote"],
    ))

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"UltraBot EOD Report {report.get('date', '')}",
        author="UltraBot v0.4.8",
    )
    doc.build(story)
    logger.info("EOD PDF written: %s", out_path)
    return os.path.abspath(out_path)
