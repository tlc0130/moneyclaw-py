# MoneyClaw Strategy Intelligence Architecture

## Purpose
Build a recommendation-first intelligence layer around the currently winning live strategy (`combined_crypto_strategy`) so MoneyClaw can:

- understand current market regime
- analyze execution logs and trade history
- compare live behavior against expectations
- recommend bounded parameter adjustments
- test changes in shadow mode before promoting them live

This is **not** a self-modifying free-for-all.
The goal is controlled adaptation with evidence, guardrails, and approval.

## Core principle
Keep the strategy thesis stable unless evidence says otherwise.

For the current setup, that means:
- preserve `combined_crypto_strategy` as the baseline
- adapt parameters and risk behavior first
- only change strategy code after observability and shadow testing are in place

## Current repo capabilities to build on
MoneyClaw already has useful primitives:

- `moneyclaw.agent.memory.Memory`
  - stores opportunities, results, daily P&L, paper positions, and paper ledger in SQLite
- `moneyclaw.data.storage.MarketStorage`
  - stores quotes and OHLCV in DuckDB
- `moneyclaw.scheduler.jobs`
  - already runs recurring jobs for data collection and daily reports
- `moneyclaw.agent.strategy_version.StrategyVersionManager`
  - supports version history and rollback for strategies
- `moneyclaw.agent.strategy_generator_v2`
  - already has AI optimization/iteration concepts

That means the architecture should extend existing storage/reporting instead of inventing a second system.

## High-level flow

```text
Market data + execution data + error/log data
                ↓
      Regime classification layer
                ↓
      Strategy performance analysis
                ↓
    Recommendation / tuning engine
                ↓
       Shadow-test / paper compare
                ↓
         Human-approved promotion
```

## Layer 1 — data inputs

### A. Market state inputs
Use current and recent market data for symbols touched by live strategies.

Capture at minimum:
- price
- short and medium trend direction
- ATR or realized volatility proxy
- breakout context (Donchian range width / position)
- RSI / momentum context
- volume/liquidity proxy where available
- spread or bid/ask quality where available

Primary sources in current repo:
- DuckDB market storage (`data/market*.duckdb`)
- feed collection already wired through scheduler jobs

### B. Execution / decision inputs
Use SQLite memory to analyze:
- opportunities created
- opportunities executed / rejected / pending
- realized or paper P&L
- execution count by strategy
- execution count by symbol
- execution count by time window
- recent streaks / drawdown patterns

Primary source in current repo:
- `data/moneyclaw.db`

### C. Reliability / log inputs
Add or normalize log-derived signals:
- symbol fetch failures
- exchange/API outages
- missing candles
- repeated retries
- stale quote windows
- web/dashboard availability
- Telegram approval friction

These should become structured analysis inputs, not just human-readable logs.

## Layer 2 — market regime detection
The market regime detector should classify each active symbol and also produce an account-level summary.

### Suggested regime dimensions
- trend: `uptrend | downtrend | range`
- volatility: `low | medium | high`
- momentum quality: `weak | normal | strong`
- breakout environment: `clean | crowded | false-break-prone`
- liquidity quality: `good | degraded`
- risk posture: `risk_on | neutral | risk_off`

### Why this matters
For `combined_crypto_strategy`, Donchian and RSI-2 behavior will not be equally effective in all conditions.

Examples:
- strong trend + healthy breakout environment → lean more on Donchian trend logic
- sideways / noisy market → reduce breakout sensitivity and rely more on mean reversion filters
- high volatility + degraded fills → reduce size, widen cooldowns, or pause specific symbols

### Deliverable
A daily and intraday regime snapshot like:

```json
{
  "symbol": "BTC/USDT",
  "trend": "uptrend",
  "volatility": "high",
  "momentum_quality": "strong",
  "breakout_environment": "clean",
  "liquidity_quality": "good",
  "risk_posture": "risk_on",
  "confidence": 0.82
}
```

## Layer 3 — strategy analytics
The analytics layer should answer:
- what the strategy is doing
- what it should be doing
- where it is underperforming
- what conditions its edge appears strongest in

### Required metrics
Per strategy and per symbol:
- total scans
- total opportunities created
- total executions
- execution rate
- approval rate
- recent P&L (1d / 7d / 30d)
- average P&L per execution
- hit rate
- max drawdown proxy
- average hold time if available
- win/loss streak behavior
- frequency of skipped trades
- frequency of data errors during decision windows

### Required breakdowns
Group results by:
- symbol
- market regime
- time-of-day bucket
- day-of-week bucket
- signal family (trend-following vs mean reversion component)
- parameter version / strategy version

### Important gap
The current DB stats are useful but incomplete for deep adaptation.
You will likely want to add tables or enriched result details for:
- regime snapshot at decision time
- parameter set hash/version
- signal subtype
- reason codes for skipped or filtered trades

## Layer 4 — recommendation engine
This layer produces **proposals**, not automatic rewrites.

### Allowed recommendation types
Safe recommendations should focus on bounded knobs:
- Donchian lookback
- RSI oversold / overbought thresholds
- cooldown duration
- max executions per symbol per day
- volatility filter threshold
- symbol allowlist / denylist
- size multiplier by regime
- pause conditions during degraded data quality

### Recommendation output shape
Each recommendation should include:
- current setting
- proposed setting
- reason
- evidence
- expected effect
- confidence
- risk note
- whether shadow test is required

Example:

```json
{
  "strategy": "combined_crypto_strategy",
  "parameter": "rsi2_entry_threshold",
  "current": 12,
  "proposed": 9,
  "reason": "Recent high-volatility trend regime is generating too many early mean-reversion entries.",
  "evidence": {
    "regime": "uptrend/high_volatility",
    "underperforming_signals": 14,
    "baseline_win_rate": 0.41,
    "recent_win_rate": 0.28
  },
  "expected_effect": "Reduce false countertrend entries.",
  "confidence": 0.74,
  "requires_shadow_test": true
}
```

## Layer 5 — shadow testing
Before changing live settings, evaluate recommendations in paper/shadow mode.

### Shadow-test goals
Compare:
- current baseline parameters
- recommended parameters
- same symbols
- same market window
- same risk rules where possible

### Promotion rule
Only promote if the candidate improves one or more of:
- risk-adjusted return
- drawdown behavior
- trade quality
- signal reliability
- operational stability

without materially worsening:
- slippage sensitivity
- overtrading
- loss clustering
- dependency on shaky market data

## Layer 6 — approval and rollout
Recommended rollout modes:

1. **Report only**
2. **Report + shadow test automatically**
3. **Ask for approval before config apply**
4. **Apply automatically within strict guardrails**

Recommended default for now:
- **2 for analysis**
- **3 for live changes**

## Suggested new components

### New scripts
- `scripts/strategy_intel_snapshot.py`
  - collects market, strategy, and reliability state into a daily snapshot
- `scripts/regime_detector.py`
  - computes regime tags for tracked symbols
- `scripts/strategy_recommendations.py`
  - generates recommendation packets from snapshots + logs + DB history
- `scripts/shadow_compare.py`
  - compares baseline vs candidate parameters

### New report outputs
- `reports/daily-strategy-intelligence.md`
- `reports/weekly-strategy-review.md`
- `reports/recommendations/<date>-combined-crypto-strategy.json`

### New storage additions (recommended)
Either extend SQLite or add a structured analysis store.
Suggested tables:
- `strategy_analysis_snapshots`
- `market_regimes`
- `strategy_recommendations`
- `parameter_sets`
- `execution_annotations`

## Minimal first implementation
If you want the smallest version that still matters, start here:

### Phase 1
- detect market regime for active symbols
- extract current strategy stats from SQLite
- parse error patterns from logs
- generate one daily markdown report

### Phase 2
- include parameter version tracking
- add recommendation packet output
- add shadow comparison against baseline

### Phase 3
- optional dashboard/API views
- optional multi-agent recommendation council
- optional config-apply path after approval

## Guardrails
Do not let the system:
- rewrite strategy code automatically without approval
- change more than one major parameter family at once
- promote a change without baseline comparison
- ignore feed reliability warnings
- optimize purely for recent P&L without drawdown/stability context

## Recommended first target
Use `combined_crypto_strategy` as the first-class target.

The first version of this system should answer:
1. What regime are BTC and other live symbols in right now?
2. How has the combined strategy behaved in that regime recently?
3. Which component appears to be helping or hurting?
4. What bounded parameter change is worth testing next?
5. Should that change stay in shadow mode or be promoted?
