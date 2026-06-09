"""LIVE order smoke test on Binance.US — proves the real protected-entry path.

This places REAL orders with REAL money (small): it buys ~$15 of BTC at market,
places a STOP_LOSS_LIMIT, cancels it, then sells the BTC back. Net cost is a few
cents in fees/spread. It exercises the exact code path the live strategy uses:
market_buy -> place_stop_loss -> cancel_order -> market_sell.

Run from the repo root on the VPS:
    ./venv/bin/python live_order_test.py
"""

import asyncio

from moneyclaw.cli import _force_ipv4

_force_ipv4()  # Binance.US needs IPv4 for signed calls (same fix the bot applies)

from moneyclaw.config.settings import Settings
from moneyclaw.execution.trading import ExchangeManager, TradeExecutor

SYMBOL = "BTC/USDT"
USD = 15.0       # notional to risk on the round-trip
STOP_PCT = 0.10  # protective stop 10% below entry


async def main() -> None:
    s = Settings()
    key = s.exchange.binanceus_api_key or s.exchange.binance_api_key
    sec = s.exchange.binanceus_secret or s.exchange.binance_secret

    em = ExchangeManager()
    em.connect("binanceus", key, sec)
    # max_order_usd=None -> the circuit-breaker cap never interferes with this test
    ex = TradeExecutor(em, dry_run=False, default_exchange="binanceus", max_order_usd=None)
    raw = em.get("binanceus")

    try:
        tk = await raw.fetch_ticker(SYMBOL)
        price = float(tk["last"])
        qty = USD / price
        print(f"[1] price={price:.2f}  ->  buy ~${USD} = {qty:.8f} BTC")

        buy = await ex.market_buy("binanceus", SYMBOL, qty)
        print(f"[2] BUY            status={buy.status} filled={buy.filled} id={buy.id}")
        if buy.status in ("failed", "rejected", "blocked"):
            print("    -> buy did not place; no position taken. Stopping.")
            return

        await asyncio.sleep(1.0)
        held = float((await raw.fetch_balance()).get("free", {}).get("BTC", 0) or 0)
        print(f"[3] free BTC held  {held:.8f}")
        if held <= 0:
            print("    -> no BTC balance detected; cannot test stop/sell. Stopping.")
            return

        stop_price = price * (1 - STOP_PCT)
        stp = await ex.place_stop_loss("binanceus", SYMBOL, held, stop_price)
        print(f"[4] STOP_LOSS      status={stp.status} id={stp.id} stop={stop_price:.2f}")
        if stp.status not in ("failed", "rejected"):
            cancelled = await ex.cancel_order("binanceus", stp.id)
            print(f"[5] CANCEL STOP    {cancelled}")
            await asyncio.sleep(1.0)
        else:
            print("    -> stop was NOT accepted by Binance.US (see status). Selling back anyway.")

        held2 = float((await raw.fetch_balance()).get("free", {}).get("BTC", 0) or 0)
        if held2 <= 0:
            print("    !! free BTC is 0 — it may still be locked by an uncancelled stop.")
            print("       Check open orders on Binance.US and cancel manually if needed.")
            return
        sell = await ex.market_sell("binanceus", SYMBOL, held2)
        print(f"[6] SELL BACK      status={sell.status} filled={sell.filled} id={sell.id}")

        final = {k: v for k, v in (await raw.fetch_balance()).get("free", {}).items() if v}
        print(f"[7] final free balances: {final}")

        buy_ok = buy.status in ("closed", "open")
        stop_ok = stp.status not in ("failed", "rejected")
        sell_ok = sell.status in ("closed", "open")
        print("\n=== RESULT ===")
        print(f"  market BUY placed & filled : {'YES' if buy_ok else 'NO'}")
        print(f"  native STOP accepted       : {'YES' if stop_ok else 'NO - Binance.US rejected the order type'}")
        print(f"  market SELL (exit) placed  : {'YES' if sell_ok else 'NO'}")
        if buy_ok and stop_ok and sell_ok:
            print("  ORDER PATH FULLY PROVEN.")
        elif buy_ok and sell_ok and not stop_ok:
            print("  Orders work, but native stops are rejected -> set place_native_stops: false")
            print("  in strategies_live/config.yaml and rely on the daily soft-stop instead.")
    finally:
        await em.close_all()


if __name__ == "__main__":
    asyncio.run(main())
