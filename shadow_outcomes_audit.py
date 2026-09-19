#!/usr/bin/env python3
"""
Shadow Outcomes Integrity Audit Script (P3 Pre-Training Gate)
Stdlib-only, read-only audit against ultrabot.db shadow_outcomes table.
Evaluates:
  1. Resolution rate
  2. features_json completeness per field
  3. Duplicate resolutions
  4. Label balance (win/loss distribution)
  5. Look-ahead leakage (timestamps & post-entry leakage keys)
  6. Signals coverage (report-only cross-check)
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Set

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def parse_args():
    parser = argparse.ArgumentParser(description="Audit shadow_outcomes table for P3 training integrity.")
    parser.add_argument("--db", default="ultrabot-web/backend/data/ultrabot.db", help="Path to SQLite database")
    parser.add_argument("--json", default="report.json", help="Path to write JSON report")
    return parser.parse_args()

def run_audit(db_path: str, json_out_path: str) -> int:
    print(f"[*] Connecting to database: {db_path} (read-only)")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception as e:
        print(f"[!] Could not open database read-only ({e}). Aborting — audit never writes.")
        return 2

    cursor = conn.cursor()

    # Check table existence
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shadow_outcomes'")
    if not cursor.fetchone():
        print("[!] Table 'shadow_outcomes' does not exist in database!")
        conn.close()
        return 1

    cursor.execute("PRAGMA table_info(shadow_outcomes)")
    columns_info = cursor.fetchall()
    columns = [col[1] for col in columns_info]
    print(f"[*] Columns found ({len(columns)}): {', '.join(columns)}")

    cursor.execute("SELECT * FROM shadow_outcomes")
    rows = cursor.fetchall()
    total_count = len(rows)
    print(f"[*] Total shadow outcomes in DB: {total_count}")

    if total_count == 0:
        print("[!] No records found in shadow_outcomes!")
        conn.close()
        return 1

    # Map column names to indices
    col_idx = {name: i for i, name in enumerate(columns)}

    # 1. Resolution Rate Check
    resolved_count = 0
    unresolved_count = 0
    outcomes_dist: Dict[str, int] = {}
    wins_count = 0
    losses_count = 0
    breakeven_count = 0

    # 2. features_json Completeness
    features_present_count = 0
    features_parse_error_count = 0
    field_counts: Dict[str, int] = {}
    null_field_counts: Dict[str, int] = {}

    # 3. Duplicate Resolution Check
    id_seen: Set[str] = set()
    dup_ids: List[str] = []
    sig_seen: Dict[str, str] = {}
    dup_sigs: List[Dict[str, str]] = []

    # 5. Look-Ahead Leakage Check
    leakage_records: List[Dict[str, Any]] = []
    forbidden_keys = {
        "pnl", "pnl_per_share", "net_pnl", "realized_pnl", "gross_pnl",
        "mfe", "mae", "exit_price", "exit_time", "exit_ts", "exit_reason",
        "outcome", "result", "label", "holding_period", "final_price"
    }

    def _is_leak_key(k: Any) -> bool:
        kl = str(k).lower()
        return (
            kl in forbidden_keys
            or "pnl" in kl
            or kl.startswith("exit_")
            or kl in {"mfe", "mae", "holding_period"}
        )

    key_leakage_count = 0
    ts_leakage_count = 0
    ts_compared = 0

    for i, r in enumerate(rows):
        row_id = r[col_idx["id"]] if "id" in col_idx else f"row#{i}"
        if row_id in id_seen:
            dup_ids.append(row_id)
        else:
            id_seen.add(row_id)

        # Signal ID duplicate check
        if "signal_id" in col_idx and r[col_idx["signal_id"]]:
            sig_id = r[col_idx["signal_id"]]
            if sig_id in sig_seen:
                dup_sigs.append({"signal_id": sig_id, "first_id": sig_seen[sig_id], "dup_id": row_id})
            else:
                sig_seen[sig_id] = row_id

        outcome = r[col_idx["outcome"]] if "outcome" in col_idx else None
        pnl = r[col_idx["pnl_per_share"]] if "pnl_per_share" in col_idx else None

        if outcome and str(outcome).strip() and str(outcome).strip().upper() != "NONE":
            resolved_count += 1
            out_str = str(outcome).strip()
            outcomes_dist[out_str] = outcomes_dist.get(out_str, 0) + 1

            if pnl is not None:
                try:
                    pnl_val = float(pnl)
                    if pnl_val > 0:
                        wins_count += 1
                    elif pnl_val < 0:
                        losses_count += 1
                    else:
                        breakeven_count += 1
                except (ValueError, TypeError):
                    pass
        else:
            unresolved_count += 1

        # Check features_json
        if "features_json" in col_idx and r[col_idx["features_json"]]:
            feat_raw = r[col_idx["features_json"]]
            try:
                feat_obj = json.loads(feat_raw)
                features_present_count += 1

                for k, v in feat_obj.items():
                    field_counts[k] = field_counts.get(k, 0) + 1
                    if v is None:
                        null_field_counts[k] = null_field_counts.get(k, 0) + 1

                # Look-ahead check: forbidden keys (widened regex/substring check)
                found_forbidden = [k for k in feat_obj if _is_leak_key(k)]
                if found_forbidden:
                    key_leakage_count += 1
                    leakage_records.append({
                        "id": row_id,
                        "reason": f"Forbidden post-entry keys in features_json: {found_forbidden}"
                    })

                # Look-ahead check: computed_at > registered_at
                comp_at_str = feat_obj.get("computed_at")
                reg_at_str = r[col_idx.get("registered_at")] if "registered_at" in col_idx else None
                if comp_at_str and reg_at_str:
                    ts_compared += 1
                    try:
                        # Clean ISO formats with timezone
                        c_ts = datetime.fromisoformat(comp_at_str.replace("Z", "+00:00"))
                        r_ts = datetime.fromisoformat(reg_at_str.replace("Z", "+00:00"))
                        # Allow up to 1 second clock jitter
                        if (c_ts - r_ts).total_seconds() > 1.0:
                            ts_leakage_count += 1
                            leakage_records.append({
                                "id": row_id,
                                "reason": f"computed_at ({comp_at_str}) > registered_at ({reg_at_str})"
                            })
                    except Exception:
                        pass
            except Exception:
                features_parse_error_count += 1

    # 6. Signals-Side Coverage (Report-Only)
    signals_count, orphans = None, None
    try:
        cur2 = conn.cursor()
        if cur2.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shadow_signals'").fetchone() \
                and "signal_id" in col_idx:
            signals_count = cur2.execute("SELECT COUNT(*) FROM shadow_signals").fetchone()[0]
            db_sigs = {r[0] for r in cur2.execute("SELECT signal_id FROM shadow_signals")}
            orphans = len(set(sig_seen) - db_sigs)
    except sqlite3.Error:
        pass

    conn.close()

    resolution_rate_pct = round((resolved_count / total_count) * 100.0, 2)
    win_rate_pct = round((wins_count / (wins_count + losses_count) * 100.0), 2) if (wins_count + losses_count) > 0 else 0.0

    print("\n" + "="*80)
    print("SHADOW OUTCOMES AUDIT REPORT (5-POINT INTEGRITY CHECK)")
    print("="*80)
    print(f"[1] RESOLUTION RATE: {resolved_count}/{total_count} ({resolution_rate_pct}%)")
    print(f"    - Resolved: {resolved_count} | Unresolved: {unresolved_count}")

    print(f"\n[2] FEATURES_JSON COMPLETENESS:")
    print(f"    - Present: {features_present_count}/{total_count} ({round(features_present_count/total_count*100, 1)}%)")
    print(f"    - Parse Errors: {features_parse_error_count}")
    print(f"    - Field Coverage:")
    for field, cnt in sorted(field_counts.items()):
        null_cnt = null_field_counts.get(field, 0)
        print(f"      * {field}: {cnt}/{features_present_count} present (null: {null_cnt})")

    print(f"\n[3] DUPLICATE RESOLUTIONS:")
    print(f"    - Duplicate IDs: {len(dup_ids)}")
    print(f"    - Duplicate Signal IDs: {len(dup_sigs)}")

    print(f"\n[4] LABEL BALANCE:")
    print(f"    - Outcomes: {outcomes_dist}")
    print(f"    - Wins: {wins_count} | Losses: {losses_count} | Breakeven: {breakeven_count}")
    print(f"    - Win Rate: {win_rate_pct}%")

    print(f"\n[5] LOOK-AHEAD LEAKAGE:")
    print(f"    - Post-entry forbidden key leaks: {key_leakage_count}")
    print(f"    - Timestamp look-ahead violations: {ts_leakage_count}")
    print(f"    - Timestamp rows compared: {ts_compared}")
    print(f"    - Total Leakage Incidents: {len(leakage_records)}")

    if signals_count is not None:
        print(f"\n[6] SIGNALS COVERAGE (REPORT-ONLY):")
        print(f"    - Total shadow signals: {signals_count}")
        print(f"    - Distinct outcome signals: {len(sig_seen)}")
        print(f"    - Orphan outcome signal IDs: {orphans}")

    # Decision Matrix
    is_blocked = False
    reasons_blocked = []
    warnings = []

    if len(dup_ids) > 0:
        is_blocked = True
        reasons_blocked.append(f"Found {len(dup_ids)} duplicate row IDs")
    if key_leakage_count > 0:
        is_blocked = True
        reasons_blocked.append(f"Found {key_leakage_count} records with post-entry keys in features_json")
    if ts_leakage_count > 0:
        is_blocked = True
        reasons_blocked.append(f"Found {ts_leakage_count} timestamp look-ahead violations")
    if resolution_rate_pct < 80.0:
        is_blocked = True
        reasons_blocked.append(f"Resolution rate {resolution_rate_pct}% is below 80% threshold")
    if features_parse_error_count > 0:
        is_blocked = True
        reasons_blocked.append(f"Found {features_parse_error_count} corrupted features_json strings")

    # Honest verifiable checks
    if "registered_at" not in col_idx:
        warnings.append("Timestamp look-ahead NOT VERIFIABLE — 'registered_at' column missing")
    elif ts_compared == 0:
        warnings.append("Timestamp look-ahead NOT VERIFIABLE — no rows had both timestamps")

    if win_rate_pct < 20.0 or win_rate_pct > 80.0:
        warnings.append(f"Heavily imbalanced win rate: {win_rate_pct}%")
    if len(dup_sigs) > 0:
        warnings.append(f"Found {len(dup_sigs)} duplicate signal_id resolutions")

    if is_blocked:
        status = "BLOCKED"
        rc = 1
    elif warnings:
        status = "CLEARED W/ WARNINGS"
        rc = 0
    else:
        status = "CLEARED"
        rc = 0

    print("\n" + "="*80)
    print(f"FINAL AUDIT STATUS: {status} (rc={rc})")
    if reasons_blocked:
        print("Blockers:")
        for b in reasons_blocked:
            print(f"  [X] {b}")
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  [!] {w}")
    print("="*80)

    # Dump JSON Report
    report = {
        "status": status,
        "exit_code": rc,
        "total_records": total_count,
        "resolution": {
            "resolved_count": resolved_count,
            "unresolved_count": unresolved_count,
            "rate_pct": resolution_rate_pct,
        },
        "features": {
            "present_count": features_present_count,
            "parse_error_count": features_parse_error_count,
            "field_counts": field_counts,
            "null_field_counts": null_field_counts,
        },
        "duplicates": {
            "duplicate_ids_count": len(dup_ids),
            "duplicate_signal_ids_count": len(dup_sigs),
        },
        "label_balance": {
            "outcomes_distribution": outcomes_dist,
            "wins_count": wins_count,
            "losses_count": losses_count,
            "breakeven_count": breakeven_count,
            "win_rate_pct": win_rate_pct,
        },
        "look_ahead": {
            "key_leakage_count": key_leakage_count,
            "timestamp_leakage_count": ts_leakage_count,
            "timestamp_rows_compared": ts_compared,
            "total_leakage_count": len(leakage_records),
        },
        "signals_coverage": {
            "signals_count": signals_count,
            "distinct_outcome_signals": len(sig_seen),
            "orphan_outcome_signal_ids": orphans,
        },
        "reasons_blocked": reasons_blocked,
        "warnings": warnings,
        "generated_at": datetime.now().isoformat(),
    }

    try:
        with open(json_out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"[*] JSON report saved to: {json_out_path}")
    except Exception as e:
        print(f"[!] Could not save JSON report: {e}")

    return rc

if __name__ == "__main__":
    args = parse_args()
    code = run_audit(args.db, args.json)
    sys.exit(code)
