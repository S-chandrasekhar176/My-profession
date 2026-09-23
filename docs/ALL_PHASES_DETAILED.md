# Comprehensive Breakdown of All UltraBot Phases

UltraBot's development and operational lifecycle is structured across two complementary frameworks:
1. **The Technical Architecture Phases**: The engineering upgrades to the data ingestion, streaming, and execution engine.
2. **The Strategic Trading & Deployment Roadmap (P0 to P9)**: The staged capital, data accumulation, machine learning integration, and live broker rollout.

---

# Section I: Technical Architecture Phases

## Phase 1: Real-Time Streaming & Event-Driven Engine (Current Branch)
* **Goal**: Transform UltraBot from a periodic 60s–180s REST-polling scanner into an asynchronous, sub-second event-driven engine.

### 1. In-Memory Tick-to-Bar Aggregator (`feeds/tick_bar_aggregator.py`)
* **Problem**: Scanning 50 watchlist symbols via REST requests consumed 50 HTTP calls every cycle, quickly hitting Fyers' limits (10 req/s, 200 req/min) and risking multi-hour broker lockouts.
* **Implementation**:
  * Ingests streaming WebSocket ticks (`LTP`, `volume`, `timestamp`) with zero REST polling.
  * Dynamically aggregates ticks into rolling **1m, 5m, and 15m** candles in RAM using `collections.deque(maxlen=200)`.
  * Computes real-time forming bars (`high = max`, `low = min`, `close = tick`, `volume += vol`).
  * Emits `BarCompletedEvent` at bar boundaries to evaluate strategies on fresh data in <10ms.
  * Supports robust historical seed backfilling and deduplication to eliminate `DATA_STALE_CANDLES` warnings.

### 2. Asynchronous Event Bus & Queue (`core/event_bus.py`)
* **Problem**: The legacy sequential loop (`sleep(60s)` $\to$ scan $\to$ check positions $\to$ `sleep(60s)`) left the engine blind between iterations.
* **Implementation**:
  * Replaced blocking loops with an `asyncio.Queue` priority event dispatcher:
    * **HIGH PRIORITY**: Real-time tick events for open positions.
    * **MEDIUM PRIORITY**: Bar-completion events triggering strategy scans for specific symbols.
    * **LOW PRIORITY**: Health heartbeats, telemetry, and background database syncing.

### 3. Continuous Tick-Level Position Monitor
* **Problem**: Trailing stops and hard stop-losses were previously evaluated once per minute, leading to significant slippage during fast market drops.
* **Implementation**:
  * Decoupled position management from strategy scanning.
  * Evaluates every open position against live price ticks arriving on WebSocket within milliseconds.

### 4. PaperBroker Short Margin-Lock Accounting (CP-07)
* **Problem**: In legacy paper trading, short selling credited cash to the balance instead of locking margin, artificially inflating capital and invalidating risk gate tests.
* **Implementation**:
  * Models real Indian equity margin requirements (20% margin lock for intraday MIS short trades).
  * Accurate deduction and release of locked capital upon trade exit.

### 5. Risk Gate Tuning for Live Market Conditions
* **G15 Volume Baseline**: Adapted volume requirements for mean-reversion strategies (`MRF`, `VC`) to require `0.30x` (and `0.50x` during mid-day lunch hours) instead of breakout-oriented `1.00x`.
* **G17 Cost Pre-Check & Actual-Size Sizing**: Enabled a realistic cost-to-risk ceiling (`70.0%`) in `defaults.local.yaml` so small positions with tight stops (e.g. ₹99 risk) are not blocked by fixed ₹60 statutory round-trip brokerage and taxes.

---

## Phase 2: Feed & Market Data Resilience

### 1. Dual-Mode FeedManager Watchdog (`feeds/feed_manager.py`)
* **Passive Traffic Mode**: Verifies data freshness from regular streaming ticks without dispatching external network pings.
* **Active Probe Mode**: If no data is received within the watchdog interval (120s), sends lightweight probe requests (`^NSEI`) to test broker connectivity.
* **Automatic Failover**: Escalates status (`HEALTHY` $\to$ `DEGRADED` $\to$ `DOWN`) and switches between primary and backup feeds after 3 consecutive failures.

### 2. Multi-Condition Frozen Feed Detection
* Prevents "silent freeze" bugs where WebSocket connections remain open but data stops advancing.
* Flags `FROZEN` status when price and timestamps stall for $\ge 5$ consecutive checks during market hours (>09:30 IST) and routes critical alerts to Telegram and desktop notifications.

### 3. F&O Option Chain Ingestion & Scenario Engine
* Continuous option chain polling with broker Greeks (`greeks=1` for Delta, Gamma, Theta, Vega, IV).
* Records snapshot records for tradable strikes (ATM $\pm$ 3 strikes).
* Integrates a Black-Scholes scenario engine as a verification tool to evaluate expected theta decay vs. target move potential.

---

# Section II: Strategic Trading & Deployment Roadmap (P0 to P9)

The 10-stage progression from initial paper trading to full capital scaling:

### P0 — First Production Paper Day & Split-Run
* **Focus**: Safe daily operations between environments.
* **Protocol**: Split-run across Sandbox (09:15–12:30) and local workstation (12:45–15:30).
* **Deliverables**: Sanitized DB state exports (scrubbing broker tokens), verified 15:35 EOD automated backups.

### P1 — M2.5 Stabilize & Accumulate (Active Stage)
* **Focus**: Live market soak-testing of real-time streaming, risk gates, and paper order mechanics.
* **Goal**: Accumulate a statistically significant dataset of shadow outcomes without risking real capital.
* **Exit Gate**: $\ge 100$ real-time resolved shadow trade samples, zero data-loss incidents, 5+ clean daily sessions.

### P2 — F&O Data Foundation (Parallel Track)
* **Focus**: Ingesting and validating options market data without trading options yet.
* **Scope**: Option chain poller on NIFTY/BANKNIFTY, Greeks sanity checks, IV percentile calculations.
* **Exit Gate**: 2+ weeks of clean options chain history; local calculations match broker-provided Greeks.

### P3 — M3a Model Build (Offline Machine Learning)
* **Focus**: Training predictive machine learning models on logged shadow outcomes.
* **Methodology**:
  * Train light gradient boosting / logistic models on point-in-time technical and order flow features.
  * Strict **walk-forward validation** (train on past, validate on future, strictly no lookahead leakage).
* **Exit Gate**: Positive precision and win-rate uplift compared to static rule-based execution.

### P4 — M3b Shadow Advisor Mode
* **Focus**: Model runs in live paper mode but holds zero veto power.
* **Mechanism**: Scores every strategy signal and attaches ML confidence scores to Telegram cards (`G21_ML`).
* **Exit Gate**: Real-time production scoring matches offline validation metrics with zero drift.

### P5 — M3c Model Earns Veto Power
* **Focus**: Granting the ML model authority to block low-probability trades.
* **Mechanism**: Signals scoring below a conservative threshold (e.g. score < 0.35) are vetoed. Hindsight tracking ensures false-veto costs remain low.
* **Exit Gate**: N consecutive sessions with lower drawdown, fewer losing trades, and flat-or-better total P&L.

### P6 — M4 Pre-Flight: Live Trading API Switch
* **Focus**: Infrastructure transition from read-only market data to live order placement.
* **Scope**:
  * Configure execution credentials, webhooks, and live order listeners.
  * Test live place/modify/cancel mechanics using pocket-change orders (1 share).
  * Verify hard kill-switches (Daily Max-Loss, Max Trades/Day).
* **Exit Gate**: 100% verification of live fill mechanics, partial fills, and error handlers.

### P7 — M4 Live Pilot (Small Capital Equity Cash)
* **Focus**: Initial real-money execution at strictly controlled micro-size.
* **Scope**: Equity cash only, smallest position size, 1–2 trades/day, ML gate active.
* **Exit Gate**: 10+ consecutive clean trading sessions where live execution matches paper fills within slippage limits.

### P8 — F&O Paper to Live
* **Focus**: Systematic options trading on liquid index contracts (NIFTY/BANKNIFTY).
* **Risk Controls**:
  * Delta band restricted to 0.50–0.65 (ATM to slight ITM).
  * Strict theta-budget gate (expected move capture must exceed theta decay + transaction costs).
  * Zero expiry-day entries in v1; defined-risk long-only option buying.
* **Exit Gate**: 100+ resolved options trades in paper mode adhering to all risk gates.

### P9 — M5 Scale & Operate
* **Focus**: Long-term capital scaling and robust production hosting.
* **Execution**:
  * Capital increases in disciplined $2\times$ steps, requiring a minimum of two consecutive profitable weeks per step.
  * Dedicated low-latency host / VPS deployment with automated supervisors.
