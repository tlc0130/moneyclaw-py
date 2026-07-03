# Daily Strategy Intelligence Report

Date: YYYY-MM-DD
Target strategy: `combined_crypto_strategy`
Mode: `LIVE | DRY_RUN | SHADOW`
Prepared at: HH:MM TZ

## 1. Executive summary
- Overall posture: `healthy | caution | degraded`
- Recommended action: `keep | tune | pause partially | pause fully | shadow-test candidate`
- Confidence: `0.00 - 1.00`
- One-line summary:
  - Example: `Trend-following conditions remain favorable, but RSI-2 entries are underperforming in high-volatility pullbacks.`

## 2. System health
- Dashboard reachable: `yes/no`
- Market data healthy: `yes/no`
- Recent feed errors: count
- Exchange connectivity notes:
- Approval backlog:
- Any repeated error signatures:

## 3. Market regime snapshot
### Account-level summary
- Risk posture:
- Volatility posture:
- Liquidity posture:

### By symbol
| Symbol | Trend | Volatility | Breakout Environment | Liquidity | Confidence | Notes |
|---|---|---|---|---|---:|---|
| BTC/USDT | uptrend | high | clean | good | 0.82 | breakout follow-through intact |
| ETH/USDT | range | medium | crowded | good | 0.69 | mixed signals |

## 4. Strategy performance snapshot
### Overall
- Total executions (lifetime):
- Executions (24h):
- Executions (7d):
- P&L (24h):
- P&L (7d):
- Average P&L / execution:
- Hit rate:
- Drawdown note:

### By symbol
| Symbol | Execs 24h | P&L 24h | Hit Rate | Regime Fit | Notes |
|---|---:|---:|---:|---|---|
| BTC/USDT | 3 | +12.40 | 0.67 | strong | trend entries doing most of the work |

## 5. Signal/component analysis
Break down the combined strategy into components if possible.

### Donchian trend component
- Observed quality:
- Best conditions:
- Failure mode:
- Confidence:

### RSI-2 mean reversion component
- Observed quality:
- Best conditions:
- Failure mode:
- Confidence:

## 6. Reliability and log analysis
- `symbol_fetch_failed` count:
- stale quote windows:
- skipped trade count:
- exchange/API instability notes:
- suspicious execution clusters:

## 7. Recommendation packet
### Recommendation A
- Type: `parameter tune | risk reduction | symbol pause | no change`
- Parameter / target:
- Current value:
- Proposed value:
- Reason:
- Evidence:
- Expected effect:
- Risk:
- Requires shadow test: `yes/no`

### Recommendation B
- Type:
- Parameter / target:
- Current value:
- Proposed value:
- Reason:
- Evidence:
- Expected effect:
- Risk:
- Requires shadow test: `yes/no`

## 8. Decision
Pick one:
- Keep current live settings
- Create shadow candidate
- Pause selected symbol(s)
- Reduce size
- Investigate feed reliability before tuning

Chosen next step:

## 9. Tomorrow’s checks
- What to watch next:
- What would trigger immediate review:
- What would justify promotion from shadow to live:
