# Daily Strategy Intelligence Report

Date: 2026-05-06
Target strategy: `combined_crypto_strategy`

## Executive summary
- Dashboard reachable: yes
- Today P&L: +0.00
- Pending approvals: 0
- Observed historical strategies: stock_dividend, crypto_dca

## Dashboard
- URL: http://127.0.0.1:8080
- Title: MoneyClaw Command Center

## Observed strategy stats
| Strategy | Opportunities | Executions | Avg P&L | Success % | Execs 24h | P&L 24h |
|---|---:|---:|---:|---:|---:|---:|
| stock_dividend | 40 | 40 | 0.0000 | 0.00 | 0 | 0.0000 |
| crypto_dca | 12 | 12 | 0.0000 | 0.00 | 0 | 0.0000 |

## Market regimes
| Symbol | Trend | Volatility | Breakout | Liquidity | Risk | Confidence | Notes |
|---|---|---|---|---|---|---:|---|
| bitcoin | uptrend | low | clean | degraded | risk_on | 0.85 | quote stream stale (2267.8m old); +5.69% over sampled window; range position=0.95 |
| ethereum | uptrend | low | clean | degraded | risk_on | 0.85 | quote stream stale (2267.7m old); +4.85% over sampled window; range position=0.95 |
| solana | uptrend | low | clean | degraded | risk_on | 0.85 | quote stream stale (2267.6m old); +2.00% over sampled window; range position=0.87 |

## Recent activity themes
- DCA: Buy $10.0 of bitcoin
- Dividend: MO yields 6.2%
- Dividend: T yields 4.3%
- Dividend: PFE yields 6.5%
- Dividend: VZ yields 6.0%

## Notes
- target strategy 'combined_crypto_strategy' is not present in historical SQLite records; current runtime may be using a newer/generated strategy path
- live strategy source resolved at C:\Users\thump\.openclaw\moneyclaw_strategies\paper_crypto_strategies.py
- runtime probe failed for BTC/USDT: RequestTimeout - binance GET https://api.binance.com/api/v3/exchangeInfo
- stale/degraded market data for: bitcoin, ethereum, solana

## Raw strategy CLI output
```text
2026-05-05 22:11:25 [info     ] plugins.discovered             module=strategies.paper_crypto_strategies strategy=combined_crypto_strategy
Found 1 strategies:
  combined_crypto_strategy  L0  risk=medium   Combined Donchian trend + RSI-2 mean reversion strategy
```
