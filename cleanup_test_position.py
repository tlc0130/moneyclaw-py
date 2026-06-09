"""Clean up after the live order smoke test: cancel any open BTC/USDT orders
(e.g. a dangling stop) and sell any free BTC back to USDT.

Run from the repo root on the VPS:
    ./venv/bin/python cleanup_test_position.py
"""

import asyncio

from moneyclaw.cli import _force_ipv4

_force_ipv4()

from moneyclaw.config.settings import Settings
from moneyclaw.execution.trading import ExchangeManager, TradeExecutor

SYMBOL = "BTC/USDT"


async def main() -> None:
    s = Settings()
    key = s.exchange.binanceus_api_key or s.exchange.binance_api_key
    sec = s.exchange.binanceus_secret or s.exchange.binance_secret

    em = ExchangeManager()
    em.connect("binanceus", key, sec)
    ex = TradeExecutor(em, dry_run=False, default_exchange="binanceus", max_order_usd=None)
    raw = em.get("binanceus")

    try:
        open_orders = await raw.fetch_open_orders(SYMBOL)
        print(f"open {SYMBOL} orders: {len(open_orders)}")
        for o in open_orders:
            ok = await ex.cancel_order("binanceus", o["id"], SYMBOL)
            print(f"  cancel {o['id']} ({o.get('type')}) -> {ok}")

        await asyncio.sleep(1.0)
        held = float((await raw.fetch_balance()).get("free", {}).get("BTC", 0) or 0)
        print(f"free BTC after cancels: {held:.8f}")
        if held > 0:
            sell = await ex.market_sell("binanceus", SYMBOL, held)
            print(f"SELL BACK -> status={sell.status} filled={sell.filled} id={sell.id}")
        else:
            print("no free BTC to sell.")

        final = {k: v for k, v in (await raw.fetch_balance()).get("free", {}).items() if v}
        print(f"final free balances: {final}")
    finally:
        await em.close_all()


if __name__ == "__main__":
    asyncio.run(main())
