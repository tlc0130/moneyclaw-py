# MoneyClaw Strategy Analysis Agents Plan

## Goal

Enable recommendation-focused agents to analyze MoneyClaw strategies and suggest changes using:

- live dashboard context at `http://localhost:8080`
- current strategy configs in `strategies/*/config.yaml`
- historical execution data from `data/moneyclaw.db`
- strategy code and version history where needed

## Recommended agent set

### 1. Financial Analyst
Use for:
- P&L interpretation
- risk / reward review
- config threshold recommendations
- comparing strategy sizing, limits, and portfolio concentration

### 2. Investment Researcher
Use for:
- thesis quality review
- asset and market assumption review
- identifying weak alerts, unrealistic thresholds, and missing catalysts
- asset-specific recommendations for BTC, ETH, dividend stocks, AAPL, gold

### 3. Experiment Tracker
Use for:
- structured strategy change proposals
- defining test plans before changing live configs
- setting success metrics for config changes
- comparing before/after performance over time

### 4. Code Reviewer
Use for:
- reviewing strategy implementation logic
- catching correctness or safety issues in strategy code
- checking whether config semantics match actual code behavior

### 5. Workflow Optimizer
Use for:
- improving the dashboard-to-analysis workflow
- suggesting better review loops, reporting cadence, and automation
- reducing manual steps for periodic strategy reviews

## Recommendation

Start with these three as the default review council:

1. Financial Analyst
2. Investment Researcher
3. Experiment Tracker

Add Code Reviewer when a recommendation touches strategy code.
Add Workflow Optimizer when you want the review process itself improved.

## Important guardrail

These agents should recommend changes first, not auto-apply them.

Safe flow:
1. analyze current strategy + history
2. produce recommendations
3. present proposed config/code diffs
4. apply only after explicit approval

## What the current repo already supports

MoneyClaw already has:
- strategy registry
- AI strategy chat interface
- strategy generation and optimization flows
- version history and rollback endpoints
- strategy detail endpoints for the dashboard

So the missing piece is not "AI exists or not".
The missing piece is a clean review workflow for recommendation-first analysis.

## Suggested next implementation

### Phase 1: safe analysis mode
Add a new dashboard/API capability for:
- `analyze all strategies`
- `analyze strategy <name>`
- `recommend changes for <name>`

Output should include:
- current config summary
- recent performance summary
- detected issues
- recommended changes
- confidence / rationale
- optional test plan

### Phase 2: optional multi-agent review
Run a small panel:
- Financial Analyst
- Investment Researcher
- Experiment Tracker

Then merge their outputs into one recommendation packet.

### Phase 3: human-approved apply path
Only after approval:
- write config edits
- or save strategy code revisions through existing versioning flow

## Current strategy observations worth reviewing first

### High priority
- `crypto_price_alert`: thresholds look mostly static and possibly stale relative to actual market regime
- `smart_rebalance`: fixed 60/30/10 allocation may be too rigid without volatility or drift context
- `crypto_funding`: threshold-only trigger may need market/risk filters

### Medium priority
- `crypto_dca`: simple and safe, but sizing cadence may still deserve review
- `stock_dividend`: watchlist should likely be thesis-reviewed, not just yield-screened

### Special review
- `appl_trading_strategy`: likely typo in strategy name (`appl` vs `aapl`) and should be reviewed for naming clarity and implementation assumptions
- `gold_trading_strategy`: review market assumptions, risk caps, and execution constraints
