from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ccxt
import pandas as pd
import structlog
import yaml

from moneyclaw.execution.trading import TradeExecutor
from moneyclaw.plugins.base import Opportunity, Result, Score, Strategy

log = structlog.get_logger()

_CONFIG_PATH = Path(__file__).with_name("config.yaml")
_FRAME_CACHE: dict[tuple[str, str, int], tuple[float, pd.DataFrame]] = {}
_CACHE_TTL_SECONDS = 900.0


@dataclass
class Position:
    symbol: str
    strategy: str
    entry: float
    qty: float
    hard_stop: float
    entry_time: datetime
    entry_fee: float
    stop_order_id: str | None = None  # native exchange stop-loss order, if placed

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "entry": self.entry,
            "qty": self.qty,
            "hard_stop": self.hard_stop,
            "entry_time": self.entry_time.isoformat(),
            "entry_fee": self.entry_fee,
            "stop_order_id": self.stop_order_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Position:
        return cls(
            symbol=str(data["symbol"]),
            strategy=str(data["strategy"]),
            entry=float(data["entry"]),
            qty=float(data["qty"]),
            hard_stop=float(data["hard_stop"]),
            entry_time=datetime.fromisoformat(str(data["entry_time"])),
            entry_fee=float(data.get("entry_fee", 0.0)),
            stop_order_id=data.get("stop_order_id"),
        )


def _load_config() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        log.exception("combined_strategy.config_error", path=str(_CONFIG_PATH))
        return {}


def _exchange(exchange_id: str = "binanceus") -> ccxt.Exchange:
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise ValueError(f"Unknown exchange: {exchange_id}")
    return exchange_class({"enableRateLimit": True, "timeout": 30000})


def _apply_entry_slippage(price: float, rate: float) -> float:
    return price * (1 + rate)


def _apply_exit_slippage(price: float, rate: float) -> float:
    return price * (1 - rate)


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    # Use NaN (float) not pd.NA (object) so the series stays float64 and fillna
    # doesn't emit the pandas downcasting FutureWarning.
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _fetch_ohlcv_paginated(exchange: ccxt.Exchange, symbol: str, timeframe: str, total_limit: int) -> list[list[float]]:
    all_candles: list[list[float]] = []
    per_call = 1000
    now_ms = exchange.milliseconds()
    tf_ms = int(exchange.parse_timeframe(timeframe) * 1000)
    since = now_ms - (total_limit * tf_ms)

    while len(all_candles) < total_limit:
        last_error: Exception | None = None
        batch: list[list[float]] = []
        for attempt in range(3):
            try:
                batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=per_call)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                wait_seconds = min(2 ** attempt, 8)
                log.warning(
                    "combined_strategy.fetch_retry",
                    symbol=symbol,
                    timeframe=timeframe,
                    attempt=attempt + 1,
                    wait_seconds=wait_seconds,
                    error=type(exc).__name__,
                )
                time.sleep(wait_seconds)
        if last_error is not None:
            raise last_error
        if not batch:
            break
        all_candles.extend(batch)
        since = int(batch[-1][0]) + tf_ms
        if len(batch) < per_call:
            break
        time.sleep(max(getattr(exchange, "rateLimit", 0), 0) / 1000)

    return all_candles[:total_limit]


def _frame(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    lookback: int,
    min_candles: int = 60,
) -> pd.DataFrame | None:
    """Fetch OHLCV as a DataFrame. ``min_candles`` is per-symbol: a young listing
    (e.g. a coin listed 4 months ago) can still trade the legs whose indicators
    it has enough history for, instead of being globally excluded."""
    cache_key = (symbol, timeframe, lookback)
    now = time.monotonic()
    cached = _FRAME_CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        df = cached[1]
        return df.copy() if len(df) >= min_candles else None

    candles = _fetch_ohlcv_paginated(exchange, symbol, timeframe, lookback)
    if not candles:
        return None
    df = pd.DataFrame(candles, columns=["time", "open", "high", "low", "close", "volume"])
    df["time_dt"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    _FRAME_CACHE[cache_key] = (now, df)
    if len(df) < min_candles:
        return None
    return df.copy()


class CombinedCryptoStrategy(Strategy):
    """Regime-adaptive long-only crypto strategy.

    BULL regime (BTC above its EMA on the working timeframe):
      - Donchian channel breakout (trend riding), ATR stop.
      - RSI-2 dip buying in per-symbol uptrends (close > trend SMA), ATR stop.

    BEAR/NEUTRAL regime:
      - Reduced-risk RSI-2 oversold bounces: deep dip (RSI2 < bear threshold)
        with the symbol still above a FAST EMA (don't catch falling knives),
        tighter stop, shorter time stop, half-size risk. This is what lets the
        bot keep trading when BTC is below its long EMA — the old version sat
        structurally flat for the entire bear window.

    Robustness rules (each fixes a previously diagnosed failure mode):
      - Exits are evaluated EVERY scan, before entries, and are never gated on
        the BTC regime data being available.
      - A BTC data failure falls back to the last known regime instead of
        aborting the whole scan (the old early-return skipped even exits).
      - Position notional is capped to a fraction of equity AND to free cash in
        the symbol's quote currency, so a tight ATR stop can never size an
        order beyond the wallet (InsufficientFunds retry loop).
      - Entries are re-quoted at execution time: market orders fire up to one
        scan interval after the signal bar closes, so the live price is checked
        against a tolerance and the stop/qty are recomputed from it.
      - Positions and processed-bar dedupe keys persist to a JSON state file
        next to this module — a restart no longer orphans open positions or
        double-buys the same signal bar.
      - Exit opportunities carry money_involved=0 (closing a position puts no
        new capital at risk) so the RiskManager's per-trade dollar cap cannot
        strand an open position.
      - Symbols are validated for existence AND active status, and any symbol
        whose latest candle is stale (delisted-but-still-listed markets) is
        skipped instead of trading on months-old data.
    """

    name = "combined_crypto_strategy"
    description = "Regime-adaptive: Donchian trend + RSI-2 dips (bull), reduced-risk oversold bounces (bear)"
    risk_level = "medium"
    min_llm_layer = 0

    def __init__(self, executor: TradeExecutor | None = None) -> None:
        cfg = _load_config()
        common = cfg.get("common", {})
        donchian = cfg.get("donchian", {})
        rsi2 = cfg.get("rsi2", {})
        bear = cfg.get("bear", {})

        self._executor = executor
        self._exchange_id = str(common.get("exchange_id", "binanceus"))
        self._exchange = _exchange(self._exchange_id)
        self._timeframe = str(common.get("timeframe", "4h"))
        self._tf_seconds = int(self._exchange.parse_timeframe(self._timeframe))
        self._lookback = int(common.get("lookback", 1000))
        self._start_balance = float(common.get("start_balance", 1000.0))
        self._balance = self._start_balance  # equity (cash + open position cost basis)
        self._free_by_currency: dict[str, float] = {"USDT": self._start_balance}
        self._risk_per_trade = float(common.get("risk_per_trade", 0.015))
        self._max_open_positions = int(common.get("max_open_positions", 4))
        self._max_portfolio_risk = float(common.get("max_portfolio_risk", 0.06))
        # Hard per-position notional cap as a fraction of equity. Keeps any single
        # order well below both the wallet and the RiskManager's max_position_ratio.
        self._max_notional_fraction = float(common.get("max_notional_fraction", 0.22))
        # Binance.US minimum order cost is ~$10; entries sized below this are
        # skipped loudly instead of being submitted and rejected.
        self._min_notional_usd = float(common.get("min_notional_usd", 12.0))
        self._fee_rate = float(common.get("fee_rate", 0.001))
        self._slippage_rate = float(common.get("slippage_rate", 0.002))
        self._btc_regime_ema = int(common.get("btc_regime_ema", 200))
        self._regime_symbol = str(common.get("regime_symbol", "BTC/USDT"))
        self._place_native_stops = bool(common.get("place_native_stops", True))
        self._stop_limit_buffer = float(common.get("stop_limit_buffer", 0.005))
        # If the live price has moved more than this past the signal entry by the
        # time we execute, skip the entry instead of chasing.
        self._entry_requote_tolerance = float(common.get("entry_requote_tolerance", 0.02))
        # Symbols whose newest candle is older than this many bars are treated as
        # dead markets (delisted pairs keep returning stale history).
        self._max_data_staleness_bars = int(common.get("max_data_staleness_bars", 3))
        self._scan_min_interval = float(common.get("scan_min_interval_seconds", 900))
        # A failed scan retries after this much time instead of burning the whole
        # interval (the old version stamped the window before doing the work).
        self._failed_scan_retry = float(common.get("failed_scan_retry_seconds", 180))
        self._state_path = Path(__file__).with_name(
            str(common.get("state_file", "combined_strategy_state.json"))
        )

        self._donchian_entry_channel = int(donchian.get("entry_channel", 20))
        self._donchian_exit_channel = int(donchian.get("exit_channel", 20))
        self._donchian_atr_period = int(donchian.get("atr_period", 14))
        self._donchian_atr_stop_mult = float(donchian.get("atr_stop_mult", 2.0))
        self._donchian_symbols = list(donchian.get("symbols", []))

        self._rsi_period = int(rsi2.get("rsi_period", 2))
        self._rsi_entry = float(rsi2.get("entry", 15))
        self._rsi_exit = float(rsi2.get("exit", 70))
        self._low_confirm_lookback = int(rsi2.get("low_confirm_lookback", 5))
        self._time_stop_bars = int(rsi2.get("time_stop_bars", 60))
        self._red_cut_bars = int(rsi2.get("red_cut_bars", 30))
        self._trend_ma = int(rsi2.get("trend_ma", 200))
        self._rsi_atr_period = int(rsi2.get("atr_period", 14))
        self._rsi_atr_stop_mult = float(rsi2.get("atr_stop_mult", 2.5))
        self._rsi_require_btc_regime = bool(rsi2.get("require_btc_regime", False))
        self._rsi_symbols = list(rsi2.get("symbols", []))

        self._bear_enabled = bool(bear.get("enabled", True))
        self._bear_rsi_entry = float(bear.get("rsi_entry", 10))
        self._bear_rsi_exit = float(bear.get("rsi_exit", 65))
        self._bear_trend_ema = int(bear.get("trend_ema", 50))
        self._bear_risk_scale = float(bear.get("risk_scale", 0.5))
        self._bear_atr_period = int(bear.get("atr_period", 14))
        self._bear_atr_stop_mult = float(bear.get("atr_stop_mult", 1.5))
        self._bear_time_stop_bars = int(bear.get("time_stop_bars", 30))
        self._bear_red_cut_bars = int(bear.get("red_cut_bars", 18))
        self._bear_symbols = list(bear.get("symbols", []))

        self._positions: dict[tuple[str, str], Position] = {}
        self._processed_actions: set[tuple[int, str, str, str]] = set()
        self._last_regime: bool | None = None
        self._all_symbols = sorted(
            set(self._donchian_symbols)
            | set(self._rsi_symbols)
            | set(self._bear_symbols)
            | {self._regime_symbol}
        )
        self._symbol_backoff_until: dict[str, float] = {}
        self._symbol_failures: dict[str, int] = {}
        self._symbols_validated = False
        self._last_full_scan: float | None = None
        self._load_state()

    # ------------------------------------------------------------------ state

    def _load_state(self) -> None:
        """Restore open positions, processed bars, and last regime after a
        restart — the old in-memory-only version orphaned every open position
        (no rule exits, untracked native stops) and re-bought live signal bars."""
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            for item in raw.get("positions", []):
                pos = Position.from_dict(item)
                self._positions[(pos.symbol, pos.strategy)] = pos
            self._processed_actions = {
                (int(a[0]), str(a[1]), str(a[2]), str(a[3]))
                for a in raw.get("processed_actions", [])
            }
            self._last_regime = raw.get("last_regime")
            if raw.get("paper_balance") is not None:
                self._balance = float(raw["paper_balance"])
            if raw.get("paper_free_cash") is not None:
                self._free_by_currency = {"USDT": float(raw["paper_free_cash"])}
            if self._positions:
                log.info(
                    "combined_strategy.state_restored",
                    positions=len(self._positions),
                    path=str(self._state_path),
                )
        except Exception:
            log.exception("combined_strategy.state_load_failed", path=str(self._state_path))

    def _save_state(self) -> None:
        try:
            state = {
                "positions": [p.to_dict() for p in self._positions.values()],
                "processed_actions": [list(a) for a in self._processed_actions],
                "last_regime": self._last_regime,
                "paper_balance": self._balance,
                "paper_free_cash": self._free_by_currency.get("USDT", 0.0),
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
            tmp.replace(self._state_path)
        except Exception:
            log.exception("combined_strategy.state_save_failed", path=str(self._state_path))

    def _prune_processed(self, newest_bar_ms: int) -> None:
        """Keep dedupe keys for recent bars only — the set otherwise grows by
        ~30 keys per bar forever."""
        horizon = newest_bar_ms - 50 * self._tf_seconds * 1000
        self._processed_actions = {
            a for a in self._processed_actions if a[0] >= horizon
        }

    # ------------------------------------------------------------------- scan

    async def scan(self) -> list[Opportunity]:
        # Size positions off REAL deployable equity in live mode (not the paper
        # start_balance); also refreshes per-currency free cash for notional caps.
        await self._refresh_balance()
        # Drop positions the exchange already stopped out, so we don't later try
        # to sell coins we no longer hold.
        await self._reconcile_stops()
        if not self._due_for_full_scan():
            return []
        result = await asyncio.to_thread(self._scan_sync)
        if result is None:
            # Scan blew up — retry sooner than the full interval instead of
            # silently losing the window.
            self._last_full_scan = (
                time.monotonic() - self._scan_min_interval + self._failed_scan_retry
            )
            return []
        self._last_full_scan = time.monotonic()
        return result

    def _due_for_full_scan(self) -> bool:
        if self._last_full_scan is None:
            return True
        return (time.monotonic() - self._last_full_scan) >= self._scan_min_interval

    async def _reconcile_stops(self) -> None:
        if self._executor is None or self._executor.dry_run:
            return
        for key, pos in list(self._positions.items()):
            if not pos.stop_order_id:
                continue
            try:
                status = await self._executor.get_order_status(
                    self._exchange_id, pos.stop_order_id, pos.symbol
                )
            except Exception:
                continue
            if status in ("closed", "filled"):
                # Stop fired on the exchange — realized PnL is already in the
                # wallet; just stop tracking the position.
                self._positions.pop(key, None)
                self._save_state()
                log.info(
                    "combined_strategy.stopped_out",
                    symbol=pos.symbol,
                    strategy=pos.strategy,
                    hard_stop=pos.hard_stop,
                )
            elif status in ("canceled", "expired", "rejected"):
                # We still hold the coins but the protective stop is gone —
                # re-place it rather than believing we're protected.
                log.warning(
                    "combined_strategy.stop_missing",
                    symbol=pos.symbol,
                    status=status,
                )
                pos.stop_order_id = None
                if self._place_native_stops:
                    stop = await self._executor.place_stop_loss(
                        self._exchange_id,
                        pos.symbol,
                        pos.qty,
                        pos.hard_stop,
                        buffer=self._stop_limit_buffer,
                    )
                    if stop.status != "failed":
                        pos.stop_order_id = stop.id
                self._save_state()

    async def _refresh_balance(self) -> None:
        """Live mode: equity = free quote cash + cost basis of open positions.

        The old version set balance to FREE cash only, which both shrank risk
        budgets after every buy and inflated portfolio-risk percentages (risk
        was divided by remaining cash instead of equity), making the strategy
        self-throttle far below its intended exposure."""
        if self._executor is None or self._executor.dry_run:
            return
        try:
            balance = await self._executor.exchange_manager.get_balance(self._exchange_id)
            free = balance.get("free", {}) or {}
            self._free_by_currency = {}
            total_free = 0.0
            for currency in ("USD", "USDT", "USDC"):
                try:
                    amt = float(free.get(currency, 0) or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                self._free_by_currency[currency] = amt
                total_free += amt
            open_cost = sum(p.entry * p.qty for p in self._positions.values())
            equity = total_free + open_cost
            if equity > 0:
                self._balance = equity
            else:
                log.warning("combined_strategy.zero_equity", exchange=self._exchange_id)
        except Exception as e:
            log.warning(
                "combined_strategy.balance_refresh_failed",
                exchange=self._exchange_id,
                error=str(e)[:200],
            )

    # ------------------------------------------------------- evaluate/execute

    async def evaluate(self, opp: Opportunity) -> Score:
        return Score(value=opp.pre_score or 0.8, threshold=0.4, reasoning=opp.title)

    async def _live_price(self, symbol: str) -> float | None:
        try:
            ticker = await asyncio.to_thread(self._exchange.fetch_ticker, symbol)
            price = ticker.get("last") or ticker.get("close")
            return float(price) if price else None
        except Exception:
            log.warning("combined_strategy.requote_failed", symbol=symbol)
            return None

    async def _free_base_qty(self, symbol: str) -> float | None:
        """Free balance of the BASE asset — market-buy fees on Binance.US are
        deducted from the bought asset, so the sellable quantity is less than
        the ordered quantity. Selling/stopping more than we hold fails."""
        if self._executor is None or self._executor.dry_run:
            return None
        try:
            balance = await self._executor.exchange_manager.get_balance(self._exchange_id)
            free = balance.get("free", {}) or {}
            base = symbol.split("/")[0]
            return float(free.get(base, 0) or 0)
        except Exception:
            return None

    async def execute(self, opp: Opportunity) -> Result:
        action = str(opp.data.get("action", ""))
        symbol = str(opp.data.get("symbol", ""))
        strategy_kind = str(opp.data.get("strategy_kind", ""))
        bar_time = int(opp.data.get("bar_time", 0) or 0)
        key = (symbol, strategy_kind)

        if action == "entry":
            return await self._execute_entry(opp, key, symbol, strategy_kind, bar_time)
        return await self._execute_exit(opp, key, symbol, strategy_kind, bar_time)

    async def _execute_entry(
        self,
        opp: Opportunity,
        key: tuple[str, str],
        symbol: str,
        strategy_kind: str,
        bar_time: int,
    ) -> Result:
        signal_entry = float(opp.data["entry"])
        stop_distance = float(opp.data["stop_distance"])
        risk_scale = float(opp.data.get("risk_scale", 1.0))

        # Re-quote: the signal bar closed up to a full scan interval ago. Anchor
        # the stop and size to the LIVE price; skip if the move has already run.
        live = await self._live_price(symbol)
        if live is None:
            if self._executor is not None and not self._executor.dry_run:
                return Result(
                    success=False,
                    details={"action": "entry", "symbol": symbol, "reason": "no_live_quote"},
                )
            live = signal_entry  # paper mode without a quote: use the modeled price
        if live > signal_entry * (1 + self._entry_requote_tolerance):
            log.info(
                "combined_strategy.entry_skipped_runaway",
                symbol=symbol,
                signal_entry=signal_entry,
                live=live,
            )
            return Result(
                success=False,
                details={"action": "entry", "symbol": symbol, "reason": "price_ran_away"},
            )
        entry = live
        hard_stop = entry - stop_distance
        if hard_stop <= 0 or hard_stop >= entry:
            return Result(
                success=False,
                details={"action": "entry", "symbol": symbol, "reason": "invalid_stop"},
            )

        sized = self._size_entry(symbol, entry, hard_stop, risk_scale)
        if sized is None:
            return Result(
                success=False,
                details={"action": "entry", "symbol": symbol, "reason": "sizing_rejected"},
            )
        qty, notional, risk_dollars = sized

        entry_fee = notional * self._fee_rate
        stop_order_id: str | None = None
        stop_protected = False
        if self._executor:
            order = await self._executor.market_buy(self._exchange_id, symbol, qty)
            if order.status in ("failed", "blocked", "rejected"):
                return Result(
                    success=False,
                    details={
                        "action": "entry",
                        "symbol": symbol,
                        "reason": f"exchange_order_{order.status}",
                    },
                )
            # Track what we can actually SELL later: the filled amount minus
            # base-currency fees (read back from the wallet when live).
            filled = order.filled if order.filled > 0 else qty
            sellable = await self._free_base_qty(symbol)
            if sellable is not None and 0 < sellable < filled:
                filled = sellable
            qty = filled
            notional = entry * qty
            entry_fee = notional * self._fee_rate
            if self._place_native_stops:
                stop = await self._executor.place_stop_loss(
                    self._exchange_id, symbol, qty, hard_stop, buffer=self._stop_limit_buffer
                )
                if stop.status == "failed":
                    log.warning(
                        "combined_strategy.stop_unprotected", symbol=symbol, hard_stop=hard_stop
                    )
                else:
                    stop_order_id = stop.id
                    stop_protected = True

        quote = symbol.split("/")[1] if "/" in symbol else "USDT"
        if self._executor is None or self._executor.dry_run:
            # Paper accounting: spend the cash. (Live mode reads the wallet.)
            self._free_by_currency[quote] = (
                self._free_by_currency.get(quote, 0.0) - notional - entry_fee
            )
            self._balance -= entry_fee
        else:
            # Debit local free cash immediately so a second entry in the same
            # tick can't oversubscribe the wallet (the next balance refresh
            # corrects this from the exchange).
            self._free_by_currency[quote] = (
                self._free_by_currency.get(quote, 0.0) - notional - entry_fee
            )

        self._positions[key] = Position(
            symbol=symbol,
            strategy=strategy_kind,
            entry=entry,
            qty=qty,
            hard_stop=hard_stop,
            entry_time=datetime.fromisoformat(str(opp.data["bar_time_iso"])),
            entry_fee=entry_fee,
            stop_order_id=stop_order_id,
        )
        self._processed_actions.add((bar_time, "entry", symbol, strategy_kind))
        self._save_state()
        return Result(
            success=True,
            profit_loss=0.0,
            details={
                "action": "entry",
                "symbol": symbol,
                "strategy_kind": strategy_kind,
                "qty": qty,
                "entry": entry,
                "hard_stop": hard_stop,
                "notional": notional,
                "risk_dollars": risk_dollars,
                "stop_protected": stop_protected,
                "tracked_balance": self._balance,
            },
        )

    async def _execute_exit(
        self,
        opp: Opportunity,
        key: tuple[str, str],
        symbol: str,
        strategy_kind: str,
        bar_time: int,
    ) -> Result:
        pos = self._positions.get(key)
        if not pos:
            return Result(
                success=False,
                details={
                    "action": "exit",
                    "symbol": symbol,
                    "strategy_kind": strategy_kind,
                    "reason": "no_open_position",
                },
            )

        exit_price = float(opp.data["exit"])
        sell_qty = pos.qty
        if self._executor:
            # Cancel the native stop first so the qty isn't locked / double-sold.
            if pos.stop_order_id:
                try:
                    await self._executor.cancel_order(self._exchange_id, pos.stop_order_id, symbol)
                except Exception:
                    log.warning("combined_strategy.stop_cancel_failed", symbol=symbol)
            # Never try to sell more than the wallet actually holds.
            sellable = await self._free_base_qty(symbol)
            if sellable is not None and 0 < sellable < sell_qty:
                sell_qty = sellable
            order = await self._executor.market_sell(self._exchange_id, symbol, sell_qty)
            if order.status == "failed":
                return Result(
                    success=False,
                    details={"action": "exit", "symbol": symbol, "reason": "exchange_order_failed"},
                )

        exit_fee = exit_price * sell_qty * self._fee_rate
        gross_pnl = (exit_price - pos.entry) * sell_qty
        net_pnl = gross_pnl - exit_fee - pos.entry_fee
        if self._executor is None or self._executor.dry_run:
            quote = symbol.split("/")[1] if "/" in symbol else "USDT"
            self._free_by_currency[quote] = (
                self._free_by_currency.get(quote, 0.0) + exit_price * sell_qty - exit_fee
            )
        self._balance += gross_pnl - exit_fee
        del self._positions[key]
        self._processed_actions.add((bar_time, "exit", symbol, strategy_kind))
        self._save_state()
        return Result(
            success=True,
            profit_loss=net_pnl,
            details={
                "action": "exit",
                "symbol": symbol,
                "strategy_kind": strategy_kind,
                "qty": sell_qty,
                "entry": pos.entry,
                "exit": exit_price,
                "exit_reason": opp.data.get("exit_reason"),
                "tracked_balance": self._balance,
            },
        )

    async def teardown(self) -> None:
        # Persist first — open positions are real holdings, not scratch state.
        self._save_state()
        self._positions.clear()
        self._processed_actions.clear()
        self._symbol_backoff_until.clear()
        self._symbol_failures.clear()

    def estimate_roi(self) -> float:
        return 1.25

    # ---------------------------------------------------------------- sizing

    def _size_entry(
        self, symbol: str, entry: float, hard_stop: float, risk_scale: float
    ) -> tuple[float, float, float] | None:
        """Risk-based sizing with hard caps. Returns (qty, notional, risk_dollars)
        or None (logged) when the entry can't be sized safely."""
        equity = max(self._balance, 1.0)
        risk_budget = equity * self._risk_per_trade * risk_scale
        stop_distance = entry - hard_stop
        if stop_distance <= 0:
            return None
        qty = risk_budget / stop_distance

        # Cap 1: never put more than max_notional_fraction of equity in one
        # position (a tight stop otherwise inflates notional past the wallet).
        max_notional = self._max_notional_fraction * equity
        # Cap 2: never spend more than the free cash in the symbol's quote
        # currency (with a small buffer for fees/price movement).
        quote = symbol.split("/")[1] if "/" in symbol else "USDT"
        free_quote = self._free_by_currency.get(quote)
        if free_quote is not None and free_quote > 0:
            max_notional = min(max_notional, free_quote * 0.98)
        if entry * qty > max_notional:
            qty = max_notional / entry

        notional = entry * qty
        risk_dollars = qty * stop_distance
        if notional < self._min_notional_usd:
            log.warning(
                "combined_strategy.entry_below_min_notional",
                symbol=symbol,
                notional=round(notional, 2),
                min_notional=self._min_notional_usd,
                free_quote=free_quote,
            )
            return None
        return qty, notional, risk_dollars

    def _current_portfolio_risk(self) -> float:
        equity = max(self._balance, 1.0)
        return sum(
            ((pos.entry - pos.hard_stop) * pos.qty) / equity for pos in self._positions.values()
        )

    def _can_open_more(self, risk_scale: float = 1.0) -> bool:
        if len(self._positions) >= self._max_open_positions:
            return False
        new_risk = self._risk_per_trade * risk_scale
        return self._current_portfolio_risk() + new_risk <= self._max_portfolio_risk

    def _has_position(self, symbol: str) -> bool:
        return any(pos.symbol == symbol for pos in self._positions.values())

    # ---------------------------------------------------------------- helpers

    def _ensure_symbols_validated(self) -> None:
        """Drop configured symbols that don't exist OR are inactive on the
        exchange (a delisted market can stay in load_markets() forever)."""
        if self._symbols_validated:
            return
        try:
            markets = self._exchange.load_markets()
        except Exception:
            log.warning("combined_strategy.markets_load_failed", exchange=self._exchange_id)
            return

        def tradable(sym: str) -> bool:
            market = markets.get(sym)
            return market is not None and market.get("active") is not False

        dropped = sorted(s for s in self._all_symbols if not tradable(s))
        if dropped:
            log.warning(
                "combined_strategy.symbols_unavailable",
                exchange=self._exchange_id,
                dropped=dropped,
            )
        if not tradable(self._regime_symbol):
            log.warning(
                "combined_strategy.regime_symbol_missing",
                exchange=self._exchange_id,
                detail=f"{self._regime_symbol} unavailable; falling back to last-known regime",
            )

        self._donchian_symbols = [s for s in self._donchian_symbols if tradable(s)]
        self._rsi_symbols = [s for s in self._rsi_symbols if tradable(s)]
        self._bear_symbols = [s for s in self._bear_symbols if tradable(s)]
        regime = {self._regime_symbol} if tradable(self._regime_symbol) else set()
        self._all_symbols = sorted(
            set(self._donchian_symbols)
            | set(self._rsi_symbols)
            | set(self._bear_symbols)
            | regime
        )
        self._symbols_validated = True

    def _required_bars(self, symbol: str) -> int:
        """Minimum history for the LEAST demanding leg the symbol belongs to.
        Per-leg NaN checks gate the rest — so a young listing (e.g. INJ with
        ~170 4h bars) trades Donchian/bear now and joins the SMA200 leg once it
        has the history, instead of being excluded from everything."""
        requirements = []
        if symbol in self._donchian_symbols:
            requirements.append(self._donchian_entry_channel + self._donchian_atr_period + 30)
        if symbol in self._rsi_symbols:
            requirements.append(self._trend_ma + 40)
        if symbol in self._bear_symbols:
            requirements.append(self._bear_trend_ema + self._bear_atr_period + 40)
        if not requirements and symbol == self._regime_symbol:
            requirements.append(self._btc_regime_ema + 40)
        return min(requirements) if requirements else 60

    def _bars_held(self, signal_time_ms: int, entry_time: datetime) -> int:
        entry_ms = int(entry_time.timestamp() * 1000)
        return max(0, int((signal_time_ms - entry_ms) / (self._tf_seconds * 1000)))

    # ------------------------------------------------------------- core scan

    def _scan_sync(self) -> list[Opportunity] | None:
        """Returns the opportunity list, or None when the scan itself failed
        (so the caller can retry sooner than the full interval)."""
        try:
            self._ensure_symbols_validated()
            now_ms = int(time.time() * 1000)
            tf_ms = self._tf_seconds * 1000
            symbol_data: dict[str, pd.DataFrame] = {}
            now = time.monotonic()
            # Include symbols of restored positions even if they were removed
            # from the config — their exits still need management.
            fetch_symbols = sorted(
                set(self._all_symbols) | {p.symbol for p in self._positions.values()}
            )
            for symbol in fetch_symbols:
                if self._symbol_backoff_until.get(symbol, 0.0) > now:
                    continue
                try:
                    df = _frame(
                        self._exchange,
                        symbol,
                        self._timeframe,
                        self._lookback,
                        min_candles=self._required_bars(symbol),
                    )
                    self._symbol_failures.pop(symbol, None)
                    self._symbol_backoff_until.pop(symbol, None)
                except Exception:
                    failures = self._symbol_failures.get(symbol, 0) + 1
                    self._symbol_failures[symbol] = failures
                    cooldown_seconds = min(300, 30 * failures)
                    self._symbol_backoff_until[symbol] = now + cooldown_seconds
                    log.warning(
                        "combined_strategy.symbol_fetch_failed",
                        symbol=symbol,
                        failures=failures,
                        cooldown_seconds=cooldown_seconds,
                    )
                    continue
                if df is None:
                    continue
                # Dead-market guard: a delisted pair keeps returning old candles.
                last_bar_ms = int(df.iloc[-1]["time"])
                if now_ms - last_bar_ms > self._max_data_staleness_bars * tf_ms:
                    log.warning(
                        "combined_strategy.stale_market_skipped",
                        symbol=symbol,
                        last_bar=str(df.iloc[-1]["time_dt"]),
                    )
                    continue

                df = df.copy()
                # Compute indicator columns for legs the symbol belongs to AND
                # for the kind of any restored position on it, so exits keep
                # working even after a symbol is removed from the config.
                pos_kinds = {p.strategy for p in self._positions.values() if p.symbol == symbol}
                if symbol in self._donchian_symbols or "donchian" in pos_kinds:
                    df["entry_high"] = (
                        df["high"].rolling(self._donchian_entry_channel).max().shift(1)
                    )
                    df["exit_low"] = df["low"].rolling(self._donchian_exit_channel).min().shift(1)
                    df["donchian_atr"] = _atr(df, self._donchian_atr_period)
                if (
                    symbol in self._rsi_symbols
                    or symbol in self._bear_symbols
                    or pos_kinds & {"rsi2", "bear_rsi"}
                ):
                    df["rsi2"] = _rsi(df["close"], self._rsi_period)
                    # Shifted: "made a new N-bar low vs PRIOR bars". The old
                    # unshifted window included the signal bar itself, which made
                    # the capitulation check trivially true.
                    df["low_prior"] = (
                        df["low"].rolling(self._low_confirm_lookback).min().shift(1)
                    )
                if symbol in self._rsi_symbols:
                    df["trend_ma"] = df["close"].rolling(self._trend_ma).mean()
                    df["rsi_atr"] = _atr(df, self._rsi_atr_period)
                if symbol in self._bear_symbols:
                    df["bear_ema"] = df["close"].ewm(span=self._bear_trend_ema, adjust=False).mean()
                    df["bear_atr"] = _atr(df, self._bear_atr_period)
                symbol_data[symbol] = df

            # --- Regime: degrade gracefully, never abort the scan -----------
            bullish: bool
            btc_df = symbol_data.get(self._regime_symbol)
            if btc_df is not None and len(btc_df) >= self._btc_regime_ema + 2:
                btc_ema = (
                    btc_df["close"].ewm(span=self._btc_regime_ema, adjust=False).mean().iloc[-2]
                )
                bullish = bool(btc_df.iloc[-2]["close"] > btc_ema)
                self._last_regime = bullish
            elif self._last_regime is not None:
                bullish = self._last_regime
                log.warning("combined_strategy.regime_data_missing_using_last", bullish=bullish)
            else:
                bullish = False
                log.warning("combined_strategy.regime_unknown_assuming_bear")

            opportunities: list[Opportunity] = []
            opportunities.extend(self._exit_opportunities(symbol_data))

            if bullish:
                opportunities.extend(self._donchian_entries(symbol_data))
            if bullish or not self._rsi_require_btc_regime:
                opportunities.extend(self._rsi_entries(symbol_data))
            if self._bear_enabled and not bullish:
                opportunities.extend(self._bear_entries(symbol_data))

            self._prune_processed(now_ms)
            self._save_state()
            return opportunities
        except Exception:
            log.exception("combined_strategy.scan_failed")
            return None

    # ------------------------------------------------------------------ exits

    def _exit_opportunities(self, symbol_data: dict[str, pd.DataFrame]) -> list[Opportunity]:
        """Rule exits for every open position. Always evaluated, never regime-
        gated, and reported with money_involved=0: closing a position is
        risk-REDUCING, so the RiskManager's per-trade dollar cap must not be
        able to strand it (the native stop stays as the backstop either way)."""
        opportunities: list[Opportunity] = []
        for (symbol, strategy_kind), pos in list(self._positions.items()):
            df = symbol_data.get(symbol)
            if df is None or len(df) < 2:
                continue
            signal = df.iloc[-2]
            bar_time = int(signal["time"])
            action_key = (bar_time, "exit", symbol, strategy_kind)
            if action_key in self._processed_actions:
                continue

            exit_price_signal: float | None = None
            exit_reason: str | None = None

            if strategy_kind == "donchian":
                exit_low = signal.get("exit_low")
                if exit_low is None or pd.isna(exit_low):
                    # No channel data (e.g. symbol dropped from config) — the
                    # hard stop must still work as a rule exit.
                    if float(signal["low"]) <= pos.hard_stop:
                        exit_price_signal = pos.hard_stop
                        exit_reason = "Hard Stop"
                else:
                    effective_exit = max(float(exit_low), pos.hard_stop)
                    if float(signal["low"]) <= effective_exit:
                        exit_price_signal = effective_exit
                        exit_reason = (
                            "Donchian Channel Exit"
                            if float(exit_low) > pos.hard_stop
                            else "Hard Stop"
                        )
            elif strategy_kind in ("rsi2", "bear_rsi"):
                bars_held = self._bars_held(bar_time, pos.entry_time)
                rsi_exit = self._rsi_exit if strategy_kind == "rsi2" else self._bear_rsi_exit
                red_cut = self._red_cut_bars if strategy_kind == "rsi2" else self._bear_red_cut_bars
                time_stop = (
                    self._time_stop_bars if strategy_kind == "rsi2" else self._bear_time_stop_bars
                )
                rsi_value = signal.get("rsi2")
                if float(signal["low"]) <= pos.hard_stop:
                    exit_price_signal = pos.hard_stop
                    exit_reason = "Hard Stop"
                elif pd.notna(rsi_value) and float(rsi_value) > rsi_exit:
                    exit_price_signal = float(signal["close"])
                    exit_reason = "RSI Exit"
                elif bars_held >= red_cut and float(signal["close"]) < pos.entry:
                    exit_price_signal = float(signal["close"])
                    exit_reason = "Red Cut"
                elif bars_held >= time_stop:
                    exit_price_signal = float(signal["close"])
                    exit_reason = "Time Stop"

            if exit_reason and exit_price_signal is not None:
                opportunities.append(
                    Opportunity(
                        strategy_name=self.name,
                        title=f"Exit {symbol} ({exit_reason})",
                        money_involved=0.0,
                        data={
                            "action": "exit",
                            "strategy_kind": strategy_kind,
                            "symbol": symbol,
                            "qty": pos.qty,
                            "entry": pos.entry,
                            "exit": _apply_exit_slippage(exit_price_signal, self._slippage_rate),
                            "exit_reason": exit_reason,
                            "bar_time": bar_time,
                            "bar_time_iso": signal["time_dt"].isoformat(),
                        },
                        pre_score=0.95,
                    )
                )
        return opportunities

    # ---------------------------------------------------------------- entries

    def _entry_opportunity(
        self,
        symbol: str,
        signal: pd.Series,
        strategy_kind: str,
        title: str,
        signal_entry: float,
        stop_distance: float,
        risk_scale: float,
        pre_score: float,
    ) -> Opportunity | None:
        bar_time = int(signal["time"])
        if (bar_time, "entry", symbol, strategy_kind) in self._processed_actions:
            return None
        hard_stop = signal_entry - stop_distance
        if hard_stop <= 0 or hard_stop >= signal_entry:
            return None
        sized = self._size_entry(symbol, signal_entry, hard_stop, risk_scale)
        if sized is None:
            return None
        qty, notional, risk_dollars = sized
        equity = max(self._balance, 1.0)
        return Opportunity(
            strategy_name=self.name,
            title=title,
            # money_involved = capital actually AT RISK (loss if the stop fires),
            # which is what the RiskManager's per-trade cap is meant to bound.
            # The full notional is carried in data for transparency/approval UIs.
            money_involved=risk_dollars,
            data={
                "action": "entry",
                "strategy_kind": strategy_kind,
                "symbol": symbol,
                "entry": signal_entry,
                "qty": qty,
                "hard_stop": hard_stop,
                "stop_distance": stop_distance,
                "risk_scale": risk_scale,
                "risk_budget": risk_dollars,
                "notional": notional,
                "position_ratio": notional / equity,
                "bar_time": bar_time,
                "bar_time_iso": signal["time_dt"].isoformat(),
            },
            pre_score=pre_score,
        )

    def _donchian_entries(self, symbol_data: dict[str, pd.DataFrame]) -> list[Opportunity]:
        opportunities: list[Opportunity] = []
        for symbol in self._donchian_symbols:
            if not self._can_open_more():
                break
            if self._has_position(symbol):
                continue
            df = symbol_data.get(symbol)
            if df is None:
                continue
            signal = df.iloc[-2]
            entry_high = signal.get("entry_high")
            atr_value = signal.get("donchian_atr")
            if pd.isna(entry_high) or pd.isna(atr_value) or float(atr_value) <= 0:
                continue
            # CLOSE above the channel, not just an intrabar wick: we can only act
            # after the bar closes, and a wick-breakout whose close fell back is a
            # failed breakout — buying it after the fact is structurally bad.
            if not float(signal["close"]) > float(entry_high):
                continue
            signal_entry = _apply_entry_slippage(float(signal["close"]), self._slippage_rate)
            opp = self._entry_opportunity(
                symbol,
                signal,
                "donchian",
                f"Enter {symbol} on Donchian breakout",
                signal_entry,
                self._donchian_atr_stop_mult * float(atr_value),
                1.0,
                0.82,
            )
            if opp:
                opportunities.append(opp)
        return opportunities

    def _rsi_entries(self, symbol_data: dict[str, pd.DataFrame]) -> list[Opportunity]:
        opportunities: list[Opportunity] = []
        for symbol in self._rsi_symbols:
            if not self._can_open_more():
                break
            if self._has_position(symbol):
                continue
            df = symbol_data.get(symbol)
            if df is None:
                continue
            signal = df.iloc[-2]
            rsi2 = signal.get("rsi2")
            trend_ma = signal.get("trend_ma")
            low_prior = signal.get("low_prior")
            atr_value = signal.get("rsi_atr")
            if (
                pd.isna(rsi2)
                or pd.isna(trend_ma)
                or pd.isna(low_prior)
                or pd.isna(atr_value)
                or float(atr_value) <= 0
            ):
                continue
            rsi_oversold = float(rsi2) < self._rsi_entry
            in_uptrend = float(signal["close"]) > float(trend_ma)
            capitulation = float(signal["low"]) <= float(low_prior)
            if not (rsi_oversold and in_uptrend and capitulation):
                continue
            signal_entry = _apply_entry_slippage(float(signal["close"]), self._slippage_rate)
            opp = self._entry_opportunity(
                symbol,
                signal,
                "rsi2",
                f"Enter {symbol} on RSI-2 mean reversion",
                signal_entry,
                self._rsi_atr_stop_mult * float(atr_value),
                1.0,
                0.8,
            )
            if opp:
                opportunities.append(opp)
        return opportunities

    def _bear_entries(self, symbol_data: dict[str, pd.DataFrame]) -> list[Opportunity]:
        """Bear-regime oversold bounces: deep RSI-2 dip, symbol still above its
        FAST EMA (avoid falling knives), half-size risk, tight stop, short time
        stop. This is the leg that keeps the bot trading when BTC is below its
        long EMA — the old strategy was structurally flat for the whole window."""
        opportunities: list[Opportunity] = []
        for symbol in self._bear_symbols:
            if not self._can_open_more(self._bear_risk_scale):
                break
            if self._has_position(symbol):
                continue
            df = symbol_data.get(symbol)
            if df is None:
                continue
            signal = df.iloc[-2]
            rsi2 = signal.get("rsi2")
            bear_ema = signal.get("bear_ema")
            low_prior = signal.get("low_prior")
            atr_value = signal.get("bear_atr")
            if (
                pd.isna(rsi2)
                or pd.isna(bear_ema)
                or pd.isna(low_prior)
                or pd.isna(atr_value)
                or float(atr_value) <= 0
            ):
                continue
            deep_dip = float(rsi2) < self._bear_rsi_entry
            stabilizing = float(signal["close"]) > float(bear_ema)
            capitulation = float(signal["low"]) <= float(low_prior)
            if not (deep_dip and stabilizing and capitulation):
                continue
            signal_entry = _apply_entry_slippage(float(signal["close"]), self._slippage_rate)
            opp = self._entry_opportunity(
                symbol,
                signal,
                "bear_rsi",
                f"Enter {symbol} on bear-regime oversold bounce",
                signal_entry,
                self._bear_atr_stop_mult * float(atr_value),
                self._bear_risk_scale,
                0.78,
            )
            if opp:
                opportunities.append(opp)
        return opportunities
