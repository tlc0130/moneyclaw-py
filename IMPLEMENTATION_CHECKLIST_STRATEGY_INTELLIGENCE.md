# Implementation Checklist — Strategy Intelligence

## Objective
Implement comprehensive market + log + performance analysis for MoneyClaw so strategy adjustments are evidence-based, bounded, and approval-driven.

## Phase 0 — align on operating mode
- [ ] Confirm that `combined_crypto_strategy` is the baseline live strategy
- [ ] Confirm whether current operation is primarily:
  - [ ] live profit seeking
  - [ ] supervised experimentation
  - [ ] paper-first optimization
- [ ] Confirm whether recommendations should be:
  - [ ] report only
  - [ ] report + shadow test
  - [ ] report + approval-to-apply

## Phase 1 — observability foundation
- [ ] Identify the exact source file / registration path for `combined_crypto_strategy`
- [ ] Map live strategy names to repo code and/or generated strategy versions
- [ ] Enumerate current config knobs for the live strategy
- [ ] Extend stats collection to include live strategy names, not just older static names
- [ ] Create `scripts/strategy_intel_snapshot.py`
- [ ] Capture from SQLite:
  - [ ] executions
  - [ ] P&L
  - [ ] success rate
  - [ ] pending approvals
  - [ ] recent history
- [ ] Capture from DuckDB:
  - [ ] latest quotes
  - [ ] recent OHLCV
  - [ ] symbol coverage quality
- [ ] Capture from logs:
  - [ ] `symbol_fetch_failed`
  - [ ] repeated retries
  - [ ] stale quotes
  - [ ] exchange/API failures

## Phase 2 — regime detection
- [ ] Create `scripts/regime_detector.py`
- [ ] Define regime tags:
  - [ ] trend
  - [ ] volatility
  - [ ] breakout environment
  - [ ] liquidity quality
  - [ ] risk posture
- [ ] Produce one snapshot per active symbol
- [ ] Store regime snapshots to file or DB
- [ ] Include confidence scores

## Phase 3 — reporting
- [ ] Generate `reports/daily-strategy-intelligence.md`
- [ ] Use the template in `reports/DAILY_STRATEGY_INTELLIGENCE_TEMPLATE.md`
- [ ] Add sections for:
  - [ ] executive summary
  - [ ] health
  - [ ] market regime
  - [ ] performance by symbol
  - [ ] component analysis
  - [ ] recommendation packet
- [ ] Add a weekly aggregation report

## Phase 4 — recommendation engine
- [ ] Create `scripts/strategy_recommendations.py`
- [ ] Start with bounded recommendations only:
  - [ ] Donchian lookback
  - [ ] RSI thresholds
  - [ ] cooldowns
  - [ ] max trades/day
  - [ ] symbol allow/deny
  - [ ] size scaling
- [ ] Require evidence for every recommendation
- [ ] Require confidence and risk notes
- [ ] Do not auto-apply

## Phase 5 — shadow testing
- [ ] Create `scripts/shadow_compare.py`
- [ ] Define baseline-vs-candidate comparison format
- [ ] Compare over same symbols and windows
- [ ] Evaluate:
  - [ ] P&L
  - [ ] hit rate
  - [ ] drawdown
  - [ ] trade count
  - [ ] operational reliability
- [ ] Mark candidate as:
  - [ ] reject
  - [ ] continue shadowing
  - [ ] ready for approval

## Phase 6 — approval workflow
- [ ] Decide where approval happens:
  - [ ] Telegram
  - [ ] dashboard
  - [ ] file-based/manual review
- [ ] Store recommendation packets with timestamps
- [ ] Record which parameter set is currently live
- [ ] Record promotions and rollbacks

## Phase 7 — versioning and rollback
- [ ] Save parameter-set snapshots with IDs/hashes
- [ ] Link recommendations to parameter sets
- [ ] Link promoted changes to observed outcomes
- [ ] Use existing strategy version manager for code changes only when needed
- [ ] Keep rollback simple and explicit

## Phase 8 — guardrails
- [ ] Never auto-change code without approval
- [ ] Never change multiple major parameter families at once
- [ ] Never promote changes without baseline comparison
- [ ] Never ignore degraded feed health
- [ ] Never optimize only for short-term P&L

## Good first implementation order
1. [ ] Find live `combined_crypto_strategy` source
2. [ ] Build snapshot script
3. [ ] Build regime detector
4. [ ] Generate daily report
5. [ ] Generate recommendation packet
6. [ ] Add shadow compare
7. [ ] Add approval flow

## Definition of success
This system is successful when it can answer, on demand:
- [ ] what regime the market is in right now
- [ ] how the live strategy is performing in that regime
- [ ] which component is helping or hurting
- [ ] what bounded adjustment is worth testing next
- [ ] whether that adjustment should remain shadow-only or move toward live approval
