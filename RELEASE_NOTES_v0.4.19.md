# v0.4.19 — Capital-accounting consistency across all tabs

## Problem (user-reported Sep-10)

Dashboard showed **Capital Used ₹13,447.65 / 2.7%** while the Trades tab showed
**Total Invested ₹67,238** — for the exact same two open positions
(FORCEMOT 2q @ 17,745.88 + RELIANCE 25q @ 1,269.86 = ₹67,238.26 notional).

## Root cause

Two different capital formulas were hardcoded in two different tabs:

| Surface | Formula (before) | Result |
|---|---|---|
| Dashboard `CAPITAL USED` / `Capital Utilization` | `Σ entry × qty × 0.2` (assumed 5x MIS margin) | ₹13,447.65 / 2.7% |
| Trades tab `TOTAL INVESTED` | `Σ entry × qty` (full notional) | ₹67,238 |
| Risk page `capital in use` fallback | `Σ entry × remaining_qty × 0.2` | 5x understated |
| Opportunity cards / store `margin` fallback | `qty × entry × 0.2` | 5x understated |

The **engine itself allocates capital on a full-notional basis with no leverage
factor anywhere**:

- `PositionSizer.calculate()` → `position_size = total_capital × fraction`,
  `quantity = position_size / entry_price` → committed size IS `entry × qty`
- G12 margin context + daily-risk: `_capital_in_use = Σ entry_price × remaining_qty`
  (engine.py:1178, engine.py:2427)
- `repo.get_capital_in_use()` = Σ `invested_amount` (= entry × qty)
- `/api/risk/status` → `capital_in_use` / `capital_usage_pct` (notional)
- `/api/dashboard` route → `total_invested`, `capital_usage_pct` (notional)

So the Dashboard was the outlier: its client-side `× 0.2` reported **5x less
capital committed than the engine's own risk accounting** — with 10 positions
(opened after the v0.4.18 max-positions raise) the gap would read ~2.7% on the
Dashboard while the engine's gates already treat ~13% as deployed. The Trades
tab was already consistent with the engine.

## Fix (frontend-only, 5 files)

Standardize every surface on the engine-consistent **full notional
(entry × qty)** and label it explicitly:

1. **`src/app/page.tsx`** — Dashboard `capitalUsed = Σ entry × qty`
   (× 0.2 removed). Utilization bar, Free Capital and risk-used now agree with
   the engine (Sep-10 case: ₹67,238 / 13.4%). Added:
   - tooltip on `Capital Used`: "Open-position exposure (entry x qty), full
     notional — same accounting as the engine and the Trades tab"
   - hint under the bar: "= open-position exposure (entry × qty).
     ≈ ₹13,448 margin at 5x MIS leverage (broker view)" — the broker-margin
     figure kept as informational context, no longer the headline.
2. **`src/app/trades/page.tsx`** — `Total Invested` card gains tooltip
   cross-referencing the Dashboard metric (value unchanged — it was correct).
3. **`src/app/risk/page.tsx`** — `capital in use` fallback drops `× 0.2`
   (only fires when the backend `capital_in_use` is unavailable).
4. **`src/app/opportunities/page.tsx`** — `margin` fallback = `qty × entry`,
   matching engine `capital_required` (`sizing.position_size`).
5. **`src/lib/store.ts`** — opportunity-store `margin` fallback = `entry × qty`.

## Post-fix Sep-10 numbers (same 2 positions)

- Capital Used: **₹67,238** (Dashboard) = **₹67,238** (Trades tab) ✔
- Capital Utilization: **13.4%** (was 2.7%)
- Free Capital: 500,000 − 67,238.26 − 154.26 = ₹4,32,607

## Verification

- `bunx tsc --noEmit` → exit 0
- Repo-wide scan: no residual `× 0.2` capital multiplications in `src/`
  (the single remaining `* 0.2` is the intentional broker-margin hint text)
- No backend changes; engine accounting untouched (it was already correct)

## Notes

- The Trades-tab screenshot also showed CURRENT = ENTRY and P&L +₹0.00 — that
  is the 4s live-quotes display overlay (v0.4.18) waiting for the first tick
  after entry / while broker quotes are unavailable; it does not affect the
  engine ledger. Separately observable, not part of this fix.
- No DB migration, no config change, no restart semantics change. Deploy at a
  flat window per the usual rule.
