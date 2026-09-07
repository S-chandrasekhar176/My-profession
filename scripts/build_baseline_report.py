#!/usr/bin/env python3
"""v0.4.12 baseline report — Milestone-1 measurement deliverable.

Answers the Phase-0 question with REAL data from shadow_outcomes:

    Which strategies work, under what conditions, and with what risk?

Honesty rules:
  - Small windows return INSUFFICIENT DATA sections, never percentages
    computed over tiny samples.
  - Legacy v0.4.11 rows carry no feature snapshot; coverage is reported
    as-is, not backfilled.
  - MFE/MAE are LTP-basis lower bounds (documented engine limitation).

Usage (from repo root):
    ultrabot-web/backend/.venv/bin/python scripts/build_baseline_report.py \
        [--days 30] [--out report.md]

Read-only: the script never writes to the database.
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from db.database import async_session_factory, init_db  # noqa: E402
from db.repository import Repository  # noqa: E402

GROUPS = ("strategy", "regime", "session", "symbol")


def _fmt_pct(v) -> str:
    return f"{v:.1f}%" if isinstance(v, (int, float)) else "n/a"


def _fmt(v, nd=2) -> str:
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "n/a"


def _section(title: str, data: dict) -> str:
    if data.get("status") != "ok":
        resolved = data.get("resolved_in_window", 0)
        return (
            f"### {title}\n\n"
            f"**INSUFFICIENT DATA** — {resolved} realtime-resolved sample(s) "
            f"in the window (minimum {data.get('min_required', 10)} needed "
            f"before percentages are shown).\n"
        )
    lines = [
        f"### {title}",
        "",
        "| Bucket | Resolved | Win% | Avg MFE | Avg MAE | Avg R(MFE) | Avg R(MAE) | Avg PnL/sh |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, b in data.get("buckets", {}).items():
        lines.append(
            f"| {name} | {b['resolved']} | {_fmt_pct(b['win_rate_pct'])} "
            f"| {_fmt(b['avg_mfe'])} | {_fmt(b['avg_mae'])} "
            f"| {_fmt(b['avg_r_mfe'])} | {_fmt(b['avg_r_mae'])} "
            f"| {_fmt(b['avg_pnl_per_share'])} |"
        )
    ov = data.get("overall") or {}
    lines.append(
        f"| **OVERALL** | {ov.get('resolved', 0)} | {_fmt_pct(ov.get('win_rate_pct'))} "
        f"| {_fmt(ov.get('avg_mfe'))} | {_fmt(ov.get('avg_mae'))} "
        f"| {_fmt(ov.get('avg_r_mfe'))} | {_fmt(ov.get('avg_r_mae'))} "
        f"| {_fmt(ov.get('avg_pnl_per_share'))} |"
    )
    return "\n".join(lines) + "\n"


async def build_report(days: int) -> str:
    async with async_session_factory() as session:
        repo = Repository(session)
        out = [
            "# UltraBot — Baseline Performance Report (v0.4.12)",
            "",
            f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"Window: last {days} day(s), realtime-verified shadow outcomes only",
            "",
            "Scope notes:",
            "- Shadow outcomes = signals that never became trades "
            "(gate-blocked / TTL-expired / user-skipped / strategy-shadow).",
            "- MFE/MAE are LTP-basis LOWER BOUNDS (no intrabar high/low in the feed).",
            "- R-multiples normalize by risk geometry |entry - stop_loss|.",
            "",
        ]

        coverage = await repo.get_feature_coverage()
        out += [
            "## Dataset quality",
            "",
            f"- Shadow rows total: **{coverage.get('rows_total', 0)}**",
            f"- Rows with feature snapshot: **{coverage.get('rows_with_features', 0)}** "
            f"({_fmt_pct(coverage.get('coverage_pct', 0.0))} coverage)",
            f"- Rows with session class: **{coverage.get('rows_with_session', 0)}**",
            "",
            "Legacy rows (pre-v0.4.12) have no features by design — coverage "
            "should climb toward ~100% of NEW rows; it is never backfilled.",
            "",
        ]

        for group in GROUPS:
            data = await repo.get_shadow_analytics(group_by=group, days=days)
            title = {
                "strategy": "By strategy",
                "regime": "By market regime",
                "session": "By session (time of day)",
                "symbol": "By symbol (top buckets)",
            }[group]
            out.append(_section(title, data))
            out.append("")

        weekly = await repo.get_shadow_weekly(group_by="strategy", weeks=8)
        out.append("### Weekly roll-up (last 8 weeks)")
        out.append("")
        if weekly.get("status") != "ok" or not weekly.get("weeks"):
            out.append("**INSUFFICIENT DATA** — no resolved weekly buckets yet.")
        else:
            out += ["| Week | Resolved | Win% |", "|---|---|---|"]
            for week, w in weekly["weeks"].items():
                out.append(f"| {week} | {w['resolved']} | {_fmt_pct(w['win_rate_pct'])} |")
        out.append("")

        clock = await repo.get_shadow_clock()
        out += [
            "## Gate-2 ML clock (today)",
            "",
            f"- realtime_resolved: **{clock.get('realtime_resolved', 0)}** / 100 required",
            f"- win rate today: {_fmt_pct(clock.get('win_rate_pct', 0.0))}",
            "",
            "Promotion rule: ML stage unlocks at >=100 realtime-resolved "
            "samples with calibration inside reliability bands.",
            "",
        ]
        return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="UltraBot baseline report (v0.4.12)")
    parser.add_argument("--days", type=int, default=30, help="Look-back window in days")
    parser.add_argument("--out", type=str, default="", help="Optional output .md path")
    args = parser.parse_args()

    asyncio.run(init_db())

    report = asyncio.run(build_report(args.days))
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"report written -> {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
