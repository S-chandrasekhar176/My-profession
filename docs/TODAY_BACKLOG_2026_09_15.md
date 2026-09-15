# Post-Market Hotfix & Enhancement Backlog
**Session Date:** 2026-09-15 (Live Market Sandbox Session)  
**Execution Window:** Today after 15:35 IST (Post-Market Flat Window)

---

## 📌 Item 1: Adaptive Partial Profit Booking for Scalp & Mean-Reversion (MRF) Trades

### 1. Problem Statement & Live Market Observation
* **Observed Behavior:**
  * During the 2026-09-15 live trading session, several mean-reversion trades (`SONACOMS`, `RADICO`, `HYUNDAI`) moved favorably towards their targets, but **no partial quantity profit booking** occurred before the trade closed or reached full target.
  * `SONACOMS`: Entry `₹779.79`, Target `₹784.67` (+0.63% move). The trade successfully triggered Stage 1 Breakeven Lock (locking SL at `₹780.18`), but closed at full target (+0.63%) without booking partial shares.
  * `HYUNDAI`: Entry `₹2,178.99`, Target `₹2,199.88` (+0.96% move). Configured Stage 2 book level was `₹2,198.26` (+1.00%), which sits beyond/at the final target.

### 2. Root Cause
* In `risk/partial_booker.py`, the 4-stage profit booking triggers use fixed absolute percentages:
  * **Stage 1:** +0.50% move in favor $\rightarrow$ Breakeven Lock (0% shares booked).
  * **Stage 2:** +1.00% move in favor $\rightarrow$ First partial book (25% shares booked).
  * **Stage 3:** +2.00% move in favor $\rightarrow$ Second partial book (30% shares booked).
  * **Stage 4:** +3.00% move in favor $\rightarrow$ Runner trail (remaining 45%).
* For **MRF (Mean Reversion Fade)** and short-duration scalp setups, the entire target distance is typically **0.30% to 0.95%**.
* Consequently, the trade hits the full target before ever reaching the +1.00% threshold required for Stage 2 partial booking.

### 3. Proposed Fix & Architecture
In the 15:35 IST flat window, upgrade `PartialBooker` to support **Fraction-of-Target (R:R) Booking** for setups with targets $< 1.00\%$ or strategy-specific tags (`MRF`, `SCALP`):

1. **Adaptive Target Scaling:**
   * If `target_move_pct < stage2_trigger_pct` (e.g. target is 0.60%–0.90%):
     * **Stage 1 (Breakeven Lock):** Trigger at $0.40 \times \text{Target}$ (e.g. +0.25%–0.35%), move SL to Entry + brokerage.
     * **Stage 2 (First Partial Book - 33%):** Trigger at $0.60 \times \text{Target}$ (e.g. +0.40%–0.55%), book 33% of quantity.
     * **Stage 3 (Second Partial Book - 33%):** Trigger at $0.80 \times \text{Target}$, book 33% of quantity, trail SL to $0.50 \times \text{Target}$.
     * **Stage 4 (Full Target / Runner - 34%):** Remainder exits at final Target or trails.
2. **Quantity Granularity Guard:**
   * For small lot equities (e.g. Qty = 3 or 4), ensure `max(1, int(qty * book_pct))` calculation guarantees integer share execution without rounding errors.

### 4. Implementation Checklist (Post 15:35 IST) - COMPLETED
- [x] Add `target_fraction_mode` or strategy-aware thresholds in `risk/partial_booker.py` (`_get_target_pct` + adaptive fraction-of-target scaling when $0.0 < \text{target\_pct} < 1.0\%$).
- [x] Ensure `check_and_book` calculates trigger levels dynamically based on `target_price` if provided.
- [x] Add unit test suite in `tests/test_partial_booker.py` (`TestAdaptiveBooking`) verifying scalp setups (0.4%–0.8% targets) trigger 4-stage partial booking.
- [x] Verify zero regression on wide-target strategies (`ORB`, `BREAKOUT`). All 9 tests passed in 0.26s.

---

## 📌 Item 2: ML Model Calibration, Candle Feature Context & Selective Vetoing

### 1. Live Validation Evidence (Observed Performance)
* **What is Working Well:**
  * **Real-time Scoring Execution:** The M3a inference engine is actively scoring candidate signals in real-time (`score_signal` running with zero event loop blockage and zero latency spikes).
  * **API & Diagnostic Health:** `/api/ml/status`, `/api/ml/metrics`, and `/api/ml/evaluations` endpoints are returning institutional scorecards (AUC 0.71, Brier Score 0.165, Calibration curves).
  * **Feature Extraction Pipeline:** Extracted 11 normalized factors across volatility, regime, session time, and options PCR/IVR.

* **Critical Flaws & Gaps Identified in Live Session:**
  * **Flaw A: Probability Saturation (91%–93% on 100% of Signals):**
    * Live queries to `/api/ml/evaluations` revealed all signals received identical, hyper-optimistic probabilities:
      * `ALKEM`: **91.6%**
      * `PHOENIXLTD`: **92.7%**
      * `VBL`: **92.8%**
      * `HYUNDAI`: **92.8%** (subsequently stopped out)
      * `DIVISLAB`: **92.8%**
    * In real-world market regimes, signals rarely have a 93% win probability.
  * **Flaw B: Zero Veto Actions (`veto_count = 0` / `favorable_count = 15`):**
    * Because probabilities were saturated above 90%, the model passed 100% of signals as `FAVORABLE`. It failed to veto lower-conviction setups (such as `HYUNDAI` or trades that stagnated into `TIME_EXIT`).
  * **Flaw C: Missing Candle-Derived Features (Zeros in Waterfall):**
    * In live evaluation records, `vwap_distance_pct = 0.0`, `trend_strength = 0.0`, `htf_trend_code = 0.0`.
    * Root Cause: When the engine evaluates a candidate signal, the point-in-time candle dataframe (`candles_df`) was not passed into `score_signal()`, causing all technical price-action features to fall back to neutral/zeros.
  * **Flaw D: VIX Weight Over-Dominance:**
    * In the attribution waterfall, VIX alone contributed **+24.6%** to the score, skewing the sigmoid activation into saturation during low-VIX (12.7) sideways regimes because standardizing scaler (`means`/`stds`) was omitted upon loading the model.

### 2. Proposed Architecture & Hotfix Plan (Post 15:35 IST) - COMPLETED
1. **Pass Candle Frame to ML Ingestion:**
   * In `core/engine.py`, ensure the active symbol's historical 5-minute candle dataframe is passed into `self.ml_engine.score_signal(signal, candles_df=df, ...)` so technical indicators (`trend_strength`, `vwap_distance`, `atr_pct`) are authentically computed.
2. **Scaler Persistence & Standardization:**
   * Persist `means` and `stds` in `CalibratedLinearModel.to_dict()` and `from_dict()` so loaded models never perform unnormalized raw feature multiplication.
3. **Dynamic Veto Activation:**
   * Handled through genuine technical feature extraction and standardized inputs.

### 3. Implementation Checklist (Post 15:35 IST) - COMPLETED
- [x] Wire symbol `candles_df` into `ml_engine.score_signal()` inside `core/engine.py`.
- [x] Implement scaler (`means` and `stds`) persistence in `ml/models.py` and `ml/inference.py`.
- [x] Validate distribution of scores across test signals (verify a healthy spread of `FAVORABLE`, `NEUTRAL`, and `VETO`).
- [x] Run regression tests in `tests/test_p4_ml_terminal.py` and `tests/test_m3a_ml_pipeline.py`. All 12 tests passed in 4.68s.

---

## 📌 Item 3: Distinguish Friction/Fee-Induced Breakeven Exits in Consecutive Loss Circuit Breaker

### 1. Problem Statement & Live Market Observation
* **Observed Behavior:**
  * Around 12:35–12:40 IST, all further trade executions abruptly halted.
  * Investigation of `/api/engine/status` showed the circuit breaker engaged:
    `can_take_new_trades: false` with `block_reason: "Max consecutive losses hit: 7/5"`.
* **Root Cause Analysis:**
  * In `risk/daily_risk_manager.py:record_trade_result()`, any trade with `pnl < 0` increments `consecutive_losses`.
  * The evaluation uses **Net P&L** (after subtracting statutory exchange fees and brokerage, ~₹25 to ~₹60 per trade).
  * Several trades (`RADICO`, `DIVISLAB`) actually exited flat or slightly positive in gross terms (+₹4.10, +₹2.73) upon hitting their 60-minute time-stop, but because statutory fees made the net P&L -₹24 and -₹49, they were classified as **full consecutive losses**.
  * When combined with other small time-stop exits, 5 consecutive "fee-induced losses" triggered the circuit breaker, locking out valid afternoon trades.

### 2. Proposed Fix - COMPLETED
1. **Breakeven Buffer:**
   * In `DailyRiskManager.record_trade_result()`, accept `gross_pnl` and introduce `fee_breakeven_tolerance_rupees` (default ₹60.0).
   * Trades exiting with `gross_pnl >= 0.0` or net losses within friction tolerance (`abs(pnl) <= fee_breakeven_tolerance_rupees`) increment `self.breakeven += 1` and preserve the streak without incrementing `consecutive_losses`.

### 3. Implementation Checklist (Post 15:35 IST) - COMPLETED
- [x] Update `record_trade_result` in `risk/daily_risk_manager.py` to accept `gross_pnl` and apply `fee_breakeven_tolerance_rupees`.
- [x] Update `core/engine.py` to pass `gross_pnl=round(pnl_amount, 2)` into `record_trade_result`.
- [x] Add unit tests in `tests/test_audit_fixes_comprehensive.py` (`test_daily_risk_manager_fee_breakeven_tolerance`) confirming flat time-exits do not trip the max consecutive loss circuit breaker. All tests passed in 2.18s.
