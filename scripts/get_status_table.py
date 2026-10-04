import os
import sys
import json
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.abspath("."))
from scripts.ub_auth_client import UBAuthClient

client = UBAuthClient()
code, status = client.authed_json("GET", "/api/engine/status")

today_str = datetime.now().strftime("%Y-%m-%d")
conn = sqlite3.connect("ultrabot-web/backend/data/ultrabot.db")
cur = conn.cursor()

# 1. Option snapshots today
cur.execute("SELECT COUNT(id) FROM option_snapshots WHERE date(timestamp) = ?", (today_str,))
snap_count = cur.fetchone()[0]

# 2. Shadow ML trades today (from shadow_outcomes and signals)
cur.execute("""
    SELECT COUNT(id), SUM(COALESCE(pnl_per_share, 0)) 
    FROM shadow_outcomes 
    WHERE date(created_at) = ? OR date(registered_at) = ?
""", (today_str, today_str))
shadow_row = cur.fetchone()
shadow_count = shadow_row[0] or 0
shadow_pnl = round(shadow_row[1] or 0.0, 2)

cur.execute("""
    SELECT strategy, outcome, COUNT(id), SUM(COALESCE(pnl_per_share, 0))
    FROM shadow_outcomes
    WHERE date(created_at) = ? OR date(registered_at) = ?
    GROUP BY strategy, outcome
""", (today_str, today_str))
shadow_details = cur.fetchall()

# 3. Signals generated today
cur.execute("""
    SELECT strategy, status, COUNT(*)
    FROM signals
    WHERE date(created_at) = ?
    GROUP BY strategy, status
""", (today_str,))
signals_today = cur.fetchall()

# 4. Trades today
cur.execute("""
    SELECT id, symbol, strategy, direction, quantity, entry_price, exit_price, pnl, net_pnl, status, entry_time, exit_time 
    FROM trades 
    WHERE date(entry_time) = ?
""", (today_str,))
trades = cur.fetchall()

# 5. Risk events today
cur.execute("""
    SELECT event_type, COUNT(id) 
    FROM risk_events 
    WHERE date(created_at) = ? 
    GROUP BY event_type
""", (today_str,))
risk_events = cur.fetchall()

res = {
    "engine_state": status.get("state") or status.get("status"),
    "mode": status.get("mode"),
    "broker": status.get("broker"),
    "regime": status.get("regime"),
    "vix": status.get("vix"),
    "nifty_price": status.get("nifty_price"),
    "feed": status.get("data_source"),
    "feed_realtime": status.get("data_source_realtime"),
    "snapshots_captured": snap_count,
    "shadow_ml_trades": shadow_count,
    "shadow_ml_pnl": shadow_pnl,
    "shadow_details": shadow_details,
    "signals_today": signals_today,
    "trades_executed": len(trades),
    "trades": trades,
    "realized_pnl_today": status.get("daily_pnl", {}).get("net_pnl", 0.0),
    "capital_in_use": status.get("risk", {}).get("capital_in_use", 0.0),
    "initial_capital": status.get("initial_capital"),
    "rejections_by_gate": status.get("rejections_by_gate", {}),
    "rejections_by_strategy": status.get("rejections_by_strategy", {}),
    "risk_events": risk_events,
    "errors_count": status.get("errors_count", 0),
}
print(json.dumps(res, indent=2))
conn.close()
