# Moneyclaw Strategy Review and Adjustment Plan

## Goal
Create a repeatable way to evaluate which Moneyclaw strategies are worth keeping live, which need tuning, and which should be paused, replaced, or combined.

## Current known reality
Static repo strategies found:

- `crypto_dca`
- `crypto_funding`
- `crypto_price_alert`
- `smart_rebalance`
- `stock_dividend`
- `appl_trading_strategy`
- `gold_trading_strategy`

Current runtime strategy output:

- `combined_crypto_strategy` — medium risk
- described as a combined Donchian trend + RSI-2 mean reversion strategy

Observed DB-backed historical stats from the helper script:

- `crypto_dca`: 12 executions
- `stock_dividend`: 40 executions
- others listed in the helper script: 0 executions
- average P&L currently shows `0.0` across those older tracked strategies

This suggests at least one of these is true:

1. live strategy usage has shifted recently
2. current runtime strategy names do not map cleanly to the older reporting script
3. performance accounting is still incomplete or mostly dry-run/notional

## Review cadence
Use three layers:

### Daily
- Is Moneyclaw up?
- Is data feed healthy?
- Did any strategy execute unexpectedly often or not at all?
- Any repeated errors like `symbol_fetch_failed`?

### Weekly
- Which strategies scanned, triggered, or executed?
- Which generated useful actions vs noise?
- Did any strategy exceed expected risk or churn?
- Do configs still match your intent?

### Monthly
- Compare strategies against each other
- prune weak/noisy strategies
- tighten sizing/risk limits
- decide whether to add, merge, or retire strategies

## What to evaluate for each strategy
For every strategy, score these areas:

1. **Execution activity**
   - total executions
   - recent executions (last 7d / 30d)
   - idle vs overactive

2. **Signal quality**
   - how often alerts/actions look reasonable
   - false positives / noise
   - whether decisions align with the strategy thesis

3. **P&L quality**
   - realized or simulated P&L
   - average P&L per execution
   - hit rate / success rate
   - drawdown behavior

4. **Operational reliability**
   - data fetch failures
   - exchange/API instability
   - missing price history
   - stalled strategy jobs

5. **Risk fit**
   - position sizing sensible?
   - frequency acceptable?
   - behavior still matches your appetite?

6. **Cost fit**
   - does the strategy require LLM calls?
   - if yes, is the value worth the inference cost?

## Decision framework
At each review, classify each strategy:

- **Keep** — working as intended
- **Tune** — good idea, bad parameters
- **Pause** — not harmful but not earning its keep
- **Replace** — thesis weak or implementation weak
- **Retire** — unnecessary, noisy, or not aligned with goals

## Immediate plan for the current setup

### Phase 1 — inventory and observability
1. Map live runtime strategies to repo folders/files
2. Confirm where `combined_crypto_strategy` is defined
3. Extend the stats script so it includes the current live strategy names
4. Record at least:
   - executions
   - recent executions
   - avg P&L
   - success rate
   - last execution time
5. Capture repeated error signatures, especially market-data failures

### Phase 2 — establish baselines
For one week, track:

- daily execution count per strategy
- daily P&L delta per strategy
- approval count
- skipped trades / blocked trades
- data fetch failures
- dashboard availability

### Phase 3 — tune configs first, not code
Start with config changes before rewriting strategy logic.

Likely first tuning targets:

- `crypto_dca/config.yaml`
  - amount size
  - symbol
  - exchange
- `smart_rebalance/config.yaml`
  - target weights
  - deviation threshold
- `stock_dividend/config.yaml`
  - watchlist
  - minimum yield threshold

If the live runtime is mostly `combined_crypto_strategy`, likely tuning knobs will include:

- Donchian lookback length
- RSI-2 thresholds
- cooldowns
- per-symbol allowlist
- sizing
- stop / exit rules

### Phase 4 — strategy-level decisions
Suggested starting decisions:

- **crypto_dca**: keep if the goal includes steady crypto accumulation and execution stays controlled
- **stock_dividend**: keep only if alerts are genuinely useful and not just decorative noise
- **crypto_funding**: pause unless exchange/data quality is stable enough for funding logic
- **smart_rebalance**: keep only if portfolio tracking and balances are trustworthy
- **combined_crypto_strategy**: evaluate carefully because it appears to be the active live logic right now

## Questions to answer before changing strategy code
1. Is Moneyclaw meant to optimize for real profit, signal exploration, or paper-trading research right now?
2. Do you want fewer, cleaner trades or more aggressive opportunity capture?
3. Which markets matter most: crypto, stocks, gold, dividends?
4. Is live execution the goal, or is this still supervised experimentation?
5. What max daily loss and max per-trade loss still feel acceptable?

## Safe adjustment order
Use this order to avoid chaotic tuning:

1. Fix observability
2. Fix data reliability
3. Tune config values
4. Compare strategy outputs
5. Pause weak strategies
6. Only then refactor strategy code

## Concrete next steps
1. Find the code path for `combined_crypto_strategy`
2. Build a better per-strategy stats snapshot script
3. Save a daily strategy report to disk
4. Review one week of behavior
5. Adjust only one strategy family at a time

## Suggested artifacts to add next
- `scripts/strategy_report.py`
- `reports/daily-strategy-summary.md`
- `reports/weekly-strategy-review.md`
- `RUNBOOK-moneyclaw-strategies.md`

## Recommendation
Do not start by rewriting strategies.

Start by making strategy behavior visible and comparable. Once the metrics and error patterns are clear, tuning decisions will be much less random.
