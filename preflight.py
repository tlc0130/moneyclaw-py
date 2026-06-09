"""One-shot deploy preflight check for MoneyClaw on the VPS.

Run from the repo root:  ./venv/bin/python preflight.py

Prints the effective config (so you can confirm the .env overrides parsed),
checks that Binance.US API keys are present, and verifies they authenticate
from this host by reading the account balance. Places no orders.
"""

from __future__ import annotations

from moneyclaw.config.settings import Settings


def main() -> None:
    s = Settings()

    print("=== CONFIG (as the app sees it) ===")
    print("default_exchange :", s.exchange.default_exchange)
    print("exchange_dry_run :", s.exchange.dry_run)
    print("risk_dry_run     :", s.risk.dry_run)
    print("max_trade_amount :", s.risk.max_trade_amount)
    print("approval_thresh  :", s.risk.approval_threshold)
    print("max_order_usd    :", s.exchange.max_order_usd)
    print("strategies_dir   :", s.strategies_dir)

    key = s.exchange.binanceus_api_key or s.exchange.binance_api_key
    sec = s.exchange.binanceus_secret or s.exchange.binance_secret
    print("\n=== KEYS ===")
    print("api key present  :", bool(key))
    print("secret present   :", bool(sec))

    print("\n=== BINANCE.US AUTH ===")
    try:
        import ccxt

        ex = ccxt.binanceus({"apiKey": key, "secret": sec, "enableRateLimit": True})
        bal = ex.fetch_balance()
        free = {k: v for k, v in bal.get("free", {}).items() if v}
        print("AUTH OK")
        print("free balances    :", free or "(empty - wallet has no free funds)")
    except Exception as e:  # noqa: BLE001 - surface any auth/network error verbatim
        print("AUTH FAILED:", type(e).__name__)
        print(str(e)[:300])


if __name__ == "__main__":
    main()
