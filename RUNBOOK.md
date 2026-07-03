# Moneyclaw Runbook

## What this is
Moneyclaw is the Python trading/finance agent project at:

- `C:\Users\thump\moneyclaw-py`

Current known live runtime:

- virtualenv: `C:\Users\thump\moneyclaw-py\.venv\Scripts\python.exe`
- launch command: `python -m moneyclaw run`
- dashboard: `http://127.0.0.1:8080`
- dashboard title: `MoneyClaw Command Center`
- listening port: `8080`

## Status
From project dir:

```powershell
cd C:\Users\thump\moneyclaw-py
C:\Users\thump\moneyclaw-py\.venv\Scripts\python.exe -m moneyclaw status
```

Quick port check:

```powershell
netstat -ano | findstr :8080
```

Quick dashboard check:

```powershell
curl http://127.0.0.1:8080
```

## Start
From project dir:

```powershell
cd C:\Users\thump\moneyclaw-py
C:\Users\thump\moneyclaw-py\.venv\Scripts\python.exe -m moneyclaw run
```

Useful variants:

```powershell
C:\Users\thump\moneyclaw-py\.venv\Scripts\python.exe -m moneyclaw run --no-telegram
C:\Users\thump\moneyclaw-py\.venv\Scripts\python.exe -m moneyclaw run --no-web
```

## Stop
Find the live Moneyclaw PID on port 8080:

```powershell
netstat -ano | findstr :8080
```

Then stop it:

```powershell
taskkill /PID <pid> /F
```

## Restart
1. Check current PID on port 8080
2. Stop it with `taskkill`
3. Start again with `python -m moneyclaw run`
4. Re-check dashboard and status

## Current known status snapshot
As of the last check:

- mode: `LIVE`
- today P&L: `+$0.00`
- pending approvals: `0`
- recent trades/actions: `5`
- recent entries included:
  - `crypto_dca` — Buy $10.0 of bitcoin
  - `stock_dividend` — PFE yields 6.6%
  - `stock_dividend` — MO yields 6.2%
  - `stock_dividend` — T yields 4.3%
  - `stock_dividend` — VZ yields 6.1%

## Strategies
The repository contains strategy directories at:

- `strategies\crypto_dca`
- `strategies\crypto_funding`
- `strategies\crypto_price_alert`
- `strategies\smart_rebalance`
- `strategies\stock_dividend`
- `strategies\appl_trading_strategy`
- `strategies\gold_trading_strategy`

But the current runtime `moneyclaw strategies` output showed:

- `combined_crypto_strategy` — `L0`, risk=`medium`
- description: `Combined Donchian trend + RSI-2 mean reversion strategy`

That means the live runtime is currently discovering or generating a strategy set that does not exactly match the simple static list above.

## Known data files
Under `data\`:

- `moneyclaw.db`
- `market.duckdb`
- `market.runtime.duckdb`

## Basic health workflow
Use this order:

1. `moneyclaw status`
2. `netstat -ano | findstr :8080`
3. `curl http://127.0.0.1:8080`
4. if behavior seems odd, inspect strategy state and DB-backed stats

## Troubleshooting notes
- If the dashboard loads but behavior looks wrong, the web layer may still be up while strategy execution is degraded.
- Previous notes mentioned `combined_strategy.symbol_fetch_failed`, suggesting market-data connectivity can be a real failure mode.
- If `moneyclaw` is not recognized, use the explicit venv Python path and `-m moneyclaw`.
- The project is editable Python code with strategy configs in `strategies\*\config.yaml`.

## Handy commands

```powershell
C:\Users\thump\moneyclaw-py\.venv\Scripts\python.exe -m moneyclaw status
C:\Users\thump\moneyclaw-py\.venv\Scripts\python.exe -m moneyclaw strategies
C:\Users\thump\moneyclaw-py\.venv\Scripts\python.exe -m moneyclaw cost
```
