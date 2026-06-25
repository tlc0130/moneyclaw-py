from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
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
# Store state in the project's data/ dir (not the source tree) so it survives
# in read-only deployments (Docker ro image layers, installed packages, etc.).
_STATE_PATH = Path(__file__).parent.parent / "data" / ".combined_strategy_state.json"
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


def _frame(exchange: ccxt.Exchange, symbol: str, timeframe: str, lookback: int) -> pd.DataFrame | None:
    cache_key = (symbol, timeframe, lookback)
    now = time.monotonic()
    cached = _FRAME_CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1].copy()

    candles = _fetch_ohlcv_paginated(exchange, symbol, timeframe, lookback)
    if len(candles) < 250:
        return None
    df = pd.DataFrame(candles, columns=["time", "open", "high", "low", "close", "volume"])
    df["time_dt"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    _FRAME_CACHE[cache_key] = (now, df)
    return df.copy()


class CombinedCryptoStrategy(Strategy):
    name = "combined_crypto_strategy"
    description = "Combined Donchian trend + RSI-2 mean reversion strategy"
    risk_level = "medium"
    min_llm_layer = 0

    def __init__(self, executor: TradeExecutor | None = None, notifier: object | None = None) -> None:
        cfg = _load_config()
        common = cfg.get("common", {})
        donchian = cfg.get("donchian", {})
        rsi2 = cfg.get("rsi2", {})

        self._executor = executor
        self._notifier = notifier
        self._last_entry_ts: float = 0.0
        self._last_dry_spell_notify: float = 0.0
        self._dry_spell_notify_hours: int = 24
        self._exchange_id = str(common.get("exchange_id", "binanceus"))
        self._exchange = _exchange(self._exchange_id)
        self._timeframe = str(common.get("timeframe", "1d"))
        self._lookback = int(common.get("lookback", 1500))
        self._start_balance = float(common.get("start_balance", 1000.0))
        self._balance = self._start_balance
        self._risk_per_trade = float(common.get("risk_per_trade", 0.015))
        self._max_open_positions = int(common.get("max_open_positions", 4))
        self._max_portfolio_risk = float(common.get("max_portfolio_risk", 0.06))
        self._fee_rate = float(common.get("fee_rate", 0.001))
        self._slippage_rate = float(common.get("slippage_rate", 0.002))
        self._btc_regime_ema = int(common.get("btc_regime_ema", 200))
        self._btc_regime_filter = bool(common.get("btc_regime_filter", True))
        self._btc_regime_tolerance = float(common.get("btc_regime_tolerance_pct", 0.0))
        self._max_trade_usd = float(common.get("max_trade_usd", 0.0))  # 0 = no cap
        # Place a native exchange stop-loss on entry so the stop is enforced 24/7
        # (not just on the daily scan). Buffer = how far below the stop the protective
        # limit sits so it stays marketable on a fast drop.
        self._place_native_stops = bool(common.get("place_native_stops", True))
        self._stop_limit_buffer = float(common.get("stop_limit_buffer", 0.005))

        self._donchian_entry_channel = int(donchian.get("entry_channel", 55))
        self._donchian_exit_channel = int(donchian.get("exit_channel", 20))
        self._donchian_atr_period = int(donchian.get("atr_period", 14))
        self._donchian_atr_stop_mult = float(donchian.get("atr_stop_mult", 2.0))
        self._donchian_symbols = list(donchian.get("symbols", []))

        self._rsi_period = int(rsi2.get("rsi_period", 2))
        self._rsi_entry = float(rsi2.get("entry", 5))
        self._rsi_exit = float(rsi2.get("exit", 70))
        self._low_confirm_lookback = int(rsi2.get("low_confirm_lookback", 5))
        self._rsi_require_capitulation = bool(rsi2.get("require_capitulation", True))
        self._time_stop_days = int(rsi2.get("time_stop_days", 10))
        self._day5_green_check = int(rsi2.get("day5_green_check", 5))
        self._trend_ma = int(rsi2.get("trend_ma", 200))
        self._rsi_atr_period = int(rsi2.get("atr_period", 14))
        self._rsi_atr_stop_mult = float(rsi2.get("atr_stop_mult", 2.5))
        self._rsi_symbols = list(rsi2.get("symbols", []))

        self._positions: dict[tuple[str, str], Position] = {}
        self._processed_actions: set[tuple[int, str, str, str]] = set()
        self._all_symbols = sorted(set(self._donchian_symbols) | set(self._rsi_symbols) | {"BTC/USDT"})
        self._symbol_backoff_until: dict[str, float] = {}
        self._symbol_failures: dict[str, int] = {}
        # #4: validate configured symbols against the exchange's real markets once,
        # so we stop repeatedly fetching pairs that don't exist on this exchange.
        self._symbols_validated = False
        # #5: this is a DAILY strategy but the agent loop ticks every ~60s. Only run
        # the (expensive) signal computation at most once per interval; cheap balance
        # refresh + stop reconciliation still happen every tick.
        self._scan_min_interval = float(common.get("scan_min_interval_seconds", 1800))
        self._last_full_scan: float | None = None
        # Track config file mtime so _check_reload_config() can hot-apply
        # changes written by the StrategyTuner without restarting the bot.
        self._config_mtime: float = 0.0
        self._load_state()

    async def scan(self) -> list[Opportunity]:
        # Size positions off REAL deployable equity in live mode (not the paper
        # start_balance). _entry_risk_budget() = self._balance * risk_per_trade, so
        # refreshing self._balance here scales every entry to the actual wallet.
        await self._refresh_balance()
        # Drop positions the exchange already stopped out, so we don't later try to
        # sell coins we no longer hold.
        await self._reconcile_stops()
        # #5: throttle the heavy daily-bar computation; balance/reconcile above stay
        # responsive every tick.
        if not self._due_for_full_scan():
            return []
        self._last_full_scan = time.monotonic()
        self._pending_dry_spell: dict | None = None  # cleared/set by _scan_sync
        log.info(
            "combined_strategy.scan_start",
            balance=round(self._balance, 2),
            open_positions=len(self._positions),
            max_open=self._max_open_positions,
        )
        opps = await asyncio.to_thread(self._scan_sync)
        # Send dry-spell Telegram alert if _scan_sync flagged one.
        report = getattr(self, "_pending_dry_spell", None)
        if report and self._notifier:
            regime = "BULLISH" if report["bullish"] else "BEARISH"
            since = f"{report['hours_since_entry']:.0f}h" if report["hours_since_entry"] else "startup"
            try:
                await self._notifier.send(
                    f"No new entries in {since}\n"
                    f"BTC regime: {regime} (${report['btc_close']:,.0f} vs EMA ${report['btc_ema']:,.0f})\n"
                    f"Open positions: {report['open_positions']}"
                )
            except Exception:
                log.warning("combined_strategy.dry_spell_notify_failed")
        return opps

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
                # Stop fired on the exchange — realized PnL is already in the wallet
                # (and _refresh_balance picks it up); just stop tracking the position.
                self._positions.pop(key, None)
                self._save_state()
                log.info(
                    "combined_strategy.stopped_out",
                    symbol=pos.symbol,
                    strategy=pos.strategy,
                    hard_stop=pos.hard_stop,
                )

    async def _refresh_balance(self) -> None:
        """In live mode, set self._balance to free quote balance on the exchange.

        Falls back to the existing (paper/tracked) balance if the executor is in
        dry_run, missing, or the balance call fails — never sizes to zero.
        """
        if self._executor is None or self._executor.dry_run:
            return
        try:
            equity = await self._executor.exchange_manager.get_available_quote_balance(
                self._exchange_id, ("USD", "USDT", "USDC")
            )
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

    async def evaluate(self, opp: Opportunity) -> Score:
        return Score(value=opp.pre_score or 0.8, threshold=0.4, reasoning=opp.title)

    async def execute(self, opp: Opportunity) -> Result:
        action = str(opp.data.get("action", ""))
        symbol = str(opp.data.get("symbol", ""))
        strategy_kind = str(opp.data.get("strategy_kind", ""))
        bar_time = int(opp.data.get("bar_time", 0) or 0)
        key = (symbol, strategy_kind)

        if action == "entry":
            qty = float(opp.data["qty"])
            entry = float(opp.data["entry"])
            hard_stop = float(opp.data["hard_stop"])
            entry_fee = entry * qty * self._fee_rate
            stop_order_id: str | None = None
            stop_protected = False
            if self._executor:
                order = await self._executor.market_buy(self._exchange_id, symbol, qty)
                if order.status in ("failed", "blocked", "rejected"):
                    return Result(
                        success=False,
                        details={
                            "action": action,
                            "symbol": symbol,
                            "reason": f"exchange_order_{order.status}",
                        },
                    )
                # Place the protective stop immediately after the fill.
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
            self._balance -= entry_fee
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
            self._processed_actions.add((bar_time, action, symbol, strategy_kind))
            self._save_state()
            return Result(
                success=True,
                profit_loss=0.0,
                details={
                    "action": action,
                    "symbol": symbol,
                    "strategy_kind": strategy_kind,
                    "qty": qty,
                    "entry": entry,
                    "hard_stop": hard_stop,
                    "stop_protected": stop_protected,
                    "tracked_balance": self._balance,
                },
            )

        pos = self._positions.get(key)
        if not pos:
            return Result(success=False, details={"action": action, "symbol": symbol, "strategy_kind": strategy_kind, "reason": "no_open_position"})

        exit_price = float(opp.data["exit"])
        if self._executor:
            # Cancel the native stop first so the qty isn't locked / double-sold.
            if pos.stop_order_id:
                try:
                    await self._executor.cancel_order(
                        self._exchange_id, pos.stop_order_id, symbol
                    )
                except Exception:
                    log.warning("combined_strategy.stop_cancel_failed", symbol=symbol)
            order = await self._executor.market_sell(self._exchange_id, symbol, pos.qty)
            if order.status == "failed":
                # Stop was already cancelled — attempt to re-protect the position so it
                # isn't left open on the exchange with no protective stop.
                if self._place_native_stops:
                    try:
                        new_stop = await self._executor.place_stop_loss(
                            self._exchange_id, symbol, pos.qty, pos.hard_stop,
                            buffer=self._stop_limit_buffer,
                        )
                        if new_stop.status != "failed":
                            pos.stop_order_id = new_stop.id
                            self._save_state()
                            log.warning(
                                "combined_strategy.sell_failed_stop_replaced",
                                symbol=symbol, hard_stop=pos.hard_stop,
                            )
                        else:
                            pos.stop_order_id = None
                            self._save_state()
                            log.error(
                                "combined_strategy.position_unprotected",
                                symbol=symbol, hard_stop=pos.hard_stop,
                            )
                    except Exception:
                        pos.stop_order_id = None
                        self._save_state()
                        log.exception("combined_strategy.stop_reprotect_failed", symbol=symbol)
                return Result(success=False, details={"action": action, "symbol": symbol, "reason": "exchange_order_failed"})
        exit_fee = exit_price * pos.qty * self._fee_rate
        gross_pnl = (exit_price - pos.entry) * pos.qty
        net_pnl = gross_pnl - exit_fee - pos.entry_fee
        self._balance += gross_pnl - exit_fee
        del self._positions[key]
        self._processed_actions.add((bar_time, action, symbol, strategy_kind))
        self._save_state()
        return Result(
            success=True,
            profit_loss=net_pnl,
            details={
                "action": action,
                "symbol": symbol,
                "strategy_kind": strategy_kind,
                "qty": pos.qty,
                "entry": pos.entry,
                "exit": exit_price,
                "exit_reason": opp.data.get("exit_reason"),
                "tracked_balance": self._balance,
            },
        )

    async def teardown(self) -> None:
        self._positions.clear()
        self._processed_actions.clear()
        self._symbol_backoff_until.clear()
        self._symbol_failures.clear()
        try:
            if _STATE_PATH.exists():
                _STATE_PATH.unlink()
        except Exception:
            log.exception("combined_strategy.state_delete_error", path=str(_STATE_PATH))

    def estimate_roi(self) -> float:
        return 1.25

    def reload_config(self) -> None:
        """Hot-reload tunable parameters from config.yaml.

        Called by the StrategyTuner after writing new config values.
        Preserves all runtime state (_positions, _balance, _processed_actions,
        _last_full_scan).  Runs synchronously — callers in async context should
        wrap in asyncio.to_thread if needed, but the operation is fast (one YAML
        read + a handful of assignments) so blocking the event loop briefly is
        acceptable here.
        """
        cfg = _load_config()
        common = cfg.get("common", {})
        donchian = cfg.get("donchian", {})
        rsi2 = cfg.get("rsi2", {})

        self._risk_per_trade = float(common.get("risk_per_trade", self._risk_per_trade))
        self._max_open_positions = int(common.get("max_open_positions", self._max_open_positions))
        self._max_portfolio_risk = float(common.get("max_portfolio_risk", self._max_portfolio_risk))
        self._scan_min_interval = float(common.get("scan_min_interval_seconds", self._scan_min_interval))
        self._btc_regime_filter = bool(common.get("btc_regime_filter", self._btc_regime_filter))
        self._btc_regime_tolerance = float(common.get("btc_regime_tolerance_pct", self._btc_regime_tolerance))
        self._max_trade_usd = float(common.get("max_trade_usd", self._max_trade_usd))

        self._donchian_entry_channel = int(donchian.get("entry_channel", self._donchian_entry_channel))
        self._donchian_exit_channel = int(donchian.get("exit_channel", self._donchian_exit_channel))
        self._donchian_atr_stop_mult = float(donchian.get("atr_stop_mult", self._donchian_atr_stop_mult))

        self._rsi_entry = float(rsi2.get("entry", self._rsi_entry))
        self._rsi_exit = float(rsi2.get("exit", self._rsi_exit))
        self._time_stop_days = int(rsi2.get("time_stop_days", self._time_stop_days))
        self._rsi_atr_stop_mult = float(rsi2.get("atr_stop_mult", self._rsi_atr_stop_mult))
        self._rsi_require_capitulation = bool(rsi2.get("require_capitulation", self._rsi_require_capitulation))

        log.info(
            "combined_strategy.config_reloaded",
            risk_per_trade=self._risk_per_trade,
            max_open_positions=self._max_open_positions,
            donchian_entry=self._donchian_entry_channel,
            rsi_entry=self._rsi_entry,
        )

    def _load_state(self) -> None:
        """Restore _positions, _processed_actions, and tracked balance from disk."""
        if not _STATE_PATH.exists():
            return
        try:
            raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            log.exception("combined_strategy.state_read_error", path=str(_STATE_PATH))
            return

        bad_positions = 0
        for record in raw.get("positions", []):
            try:
                key_list, pos_dict = record
                pos_dict["entry_time"] = datetime.fromisoformat(pos_dict["entry_time"])
                key = (str(key_list[0]), str(key_list[1]))
                self._positions[key] = Position(**pos_dict)
            except Exception:
                bad_positions += 1
                log.warning("combined_strategy.state_bad_position", record=str(record)[:200])
        if bad_positions:
            log.error("combined_strategy.state_partial_restore", skipped=bad_positions)

        cutoff_ms = int(time.time() * 1000) - 7 * 86_400_000
        for entry in raw.get("processed_actions", []):
            try:
                bar_time_ms, action, symbol, strategy_kind = entry
                if int(bar_time_ms) >= cutoff_ms:
                    self._processed_actions.add(
                        (int(bar_time_ms), str(action), str(symbol), str(strategy_kind))
                    )
            except Exception:
                pass

        if "balance" in raw:
            try:
                self._balance = float(raw["balance"])
            except (TypeError, ValueError):
                pass

        log.info(
            "combined_strategy.state_loaded",
            positions=len(self._positions),
            processed_actions=len(self._processed_actions),
            balance=self._balance,
        )

    def _save_state(self) -> None:
        """Atomically persist _positions and _processed_actions to the JSON state file."""
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            cutoff_ms = int(time.time() * 1000) - 7 * 86_400_000
            pruned_actions = [
                list(entry)
                for entry in self._processed_actions
                if entry[0] >= cutoff_ms
            ]

            pos_records = []
            for (symbol, strategy_kind), pos in self._positions.items():
                pos_dict = asdict(pos)
                pos_dict["entry_time"] = pos.entry_time.isoformat()
                pos_records.append([[symbol, strategy_kind], pos_dict])

            payload = {
                "positions": pos_records,
                "processed_actions": pruned_actions,
                "balance": self._balance,
            }
            tmp = _STATE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(_STATE_PATH)
        except Exception:
            log.exception("combined_strategy.state_save_error", path=str(_STATE_PATH))

    def _ensure_symbols_validated(self) -> None:
        """#4: drop configured symbols that don't exist on the exchange so we don't
        waste fetches + log noise on phantom pairs. Runs once (sync, in the scan
        thread); retries on a later scan if the markets call fails."""
        if self._symbols_validated:
            return
        try:
            markets = self._exchange.load_markets()
        except Exception:
            log.warning("combined_strategy.markets_load_failed", exchange=self._exchange_id)
            return
        available = set(markets.keys())

        dropped = sorted(s for s in self._all_symbols if s not in available)
        if dropped:
            log.warning(
                "combined_strategy.symbols_unavailable",
                exchange=self._exchange_id,
                dropped=dropped,
            )
        if "BTC/USDT" not in available:
            log.error(
                "combined_strategy.regime_symbol_missing",
                exchange=self._exchange_id,
                detail="BTC/USDT absent; BTC regime filter will block all entries",
            )

        self._donchian_symbols = [s for s in self._donchian_symbols if s in available]
        self._rsi_symbols = [s for s in self._rsi_symbols if s in available]
        regime = {"BTC/USDT"} if "BTC/USDT" in available else set()
        self._all_symbols = sorted(set(self._donchian_symbols) | set(self._rsi_symbols) | regime)
        self._symbols_validated = True

    def _scan_sync(self) -> list[Opportunity]:
        try:
            self._ensure_symbols_validated()
            symbol_data: dict[str, pd.DataFrame] = {}
            now = time.monotonic()
            for symbol in self._all_symbols:
                if self._symbol_backoff_until.get(symbol, 0.0) > now:
                    continue
                try:
                    df = _frame(self._exchange, symbol, self._timeframe, self._lookback)
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
                if df is None or len(df) < self._trend_ma + 50:
                    continue

                df = df.copy()
                if symbol in self._donchian_symbols:
                    df["entry_high"] = df["high"].rolling(self._donchian_entry_channel).max().shift(1)
                    df["exit_low"] = df["low"].rolling(self._donchian_exit_channel).min().shift(1)
                    df["donchian_atr"] = _atr(df, self._donchian_atr_period)
                if symbol in self._rsi_symbols:
                    df["rsi2"] = _rsi(df["close"], self._rsi_period)
                    # Use EWM (same as BTC regime filter) so both trend checks
                    # respond to price at the same speed.
                    df["trend_ma"] = df["close"].ewm(span=self._trend_ma, adjust=False).mean()
                    df["low_5d"] = df["low"].rolling(self._low_confirm_lookback).min()
                    df["rsi_atr"] = _atr(df, self._rsi_atr_period)
                symbol_data[symbol] = df

            btc_df = symbol_data.get("BTC/USDT")
            if btc_df is None or len(btc_df) < self._btc_regime_ema + 2:
                return []
            btc_ema = btc_df["close"].ewm(span=self._btc_regime_ema, adjust=False).mean().iloc[-2]
            if not self._btc_regime_filter:
                bullish = True
            else:
                btc_close = float(btc_df.iloc[-2]["close"])
                tolerance_floor = float(btc_ema) * (1.0 - self._btc_regime_tolerance)
                bullish = btc_close >= tolerance_floor
            log.info(
                "combined_strategy.scan_regime",
                bullish=bullish,
                btc_close=round(float(btc_df.iloc[-2]["close"]), 0),
                btc_ema=round(float(btc_ema), 0),
                regime_filter=self._btc_regime_filter,
                tolerance_pct=self._btc_regime_tolerance,
            )

            opportunities: list[Opportunity] = []

            for (symbol, strategy_kind), pos in list(self._positions.items()):
                df = symbol_data.get(symbol)
                if df is None:
                    continue
                signal = df.iloc[-2]
                bar_time = int(signal["time"])
                action_key = (bar_time, "exit", symbol, strategy_kind)
                if action_key in self._processed_actions:
                    continue

                if strategy_kind == "donchian":
                    exit_low = signal.get("exit_low")
                    if pd.isna(exit_low):
                        continue
                    effective_exit = max(float(exit_low), pos.hard_stop)
                    if float(signal["low"]) <= effective_exit:
                        actual_exit = _apply_exit_slippage(effective_exit, self._slippage_rate)
                        opportunities.append(
                            Opportunity(
                                strategy_name=self.name,
                                title=f"Exit {symbol} on Donchian/stop trigger",
                                money_involved=max(pos.qty * actual_exit, 0.0),
                                data={
                                    "action": "exit",
                                    "strategy_kind": strategy_kind,
                                    "symbol": symbol,
                                    "qty": pos.qty,
                                    "entry": pos.entry,
                                    "exit": actual_exit,
                                    "exit_reason": "Donchian Channel Exit" if float(exit_low) > pos.hard_stop else "Hard Stop",
                                    "bar_time": bar_time,
                                    "bar_time_iso": signal["time_dt"].isoformat(),
                                },
                                pre_score=0.95,
                            )
                        )
                elif strategy_kind == "rsi2":
                    days_held = (signal["time_dt"].to_pydatetime() - pos.entry_time).days
                    exit_price_signal: float | None = None
                    exit_reason: str | None = None
                    if float(signal["low"]) <= pos.hard_stop:
                        exit_price_signal = pos.hard_stop
                        exit_reason = "Hard Stop"
                    elif pd.notna(signal.get("rsi2")) and float(signal["rsi2"]) > self._rsi_exit:
                        exit_price_signal = float(signal["close"])
                        exit_reason = "RSI Exit"
                    elif days_held >= self._day5_green_check and float(signal["close"]) < pos.entry:
                        exit_price_signal = float(signal["close"])
                        exit_reason = "Day-5 Red Cut"
                    elif days_held >= self._time_stop_days:
                        exit_price_signal = float(signal["close"])
                        exit_reason = "Time Stop"

                    if exit_reason and exit_price_signal is not None:
                        opportunities.append(
                            Opportunity(
                                strategy_name=self.name,
                                title=f"Exit {symbol} on RSI-2 rule ({exit_reason})",
                                money_involved=max(pos.qty * exit_price_signal, 0.0),
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
                                pre_score=0.94,
                            )
                        )

            for symbol in self._donchian_symbols:
                if not self._can_open_more():
                    break
                key = (symbol, "donchian")
                if key in self._positions:
                    continue
                df = symbol_data.get(symbol)
                if df is None:
                    continue
                signal = df.iloc[-2]
                bar_time = int(signal["time"])
                action_key = (bar_time, "entry", symbol, "donchian")
                if action_key in self._processed_actions:
                    continue

                entry_high = signal.get("entry_high")
                atr_value = signal.get("donchian_atr")
                if pd.isna(entry_high) or pd.isna(atr_value) or float(atr_value) <= 0:
                    continue
                if not bullish or not (float(signal["high"]) > float(entry_high)):
                    continue

                actual_entry = _apply_entry_slippage(float(entry_high), self._slippage_rate)
                hard_stop = actual_entry - self._donchian_atr_stop_mult * float(atr_value)
                if hard_stop <= 0 or hard_stop >= actual_entry:
                    continue
                risk_budget = self._entry_risk_budget()
                qty = risk_budget / (actual_entry - hard_stop)
                position_ratio = (actual_entry * qty) / max(self._balance, 1.0)
                opportunities.append(
                    Opportunity(
                        strategy_name=self.name,
                        title=f"Enter {symbol} on Donchian breakout",
                        money_involved=risk_budget,
                        data={
                            "action": "entry",
                            "strategy_kind": "donchian",
                            "symbol": symbol,
                            "entry": actual_entry,
                            "qty": qty,
                            "hard_stop": hard_stop,
                            "risk_budget": risk_budget,
                            "position_ratio": position_ratio,
                            "bar_time": bar_time,
                            "bar_time_iso": signal["time_dt"].isoformat(),
                        },
                        pre_score=0.82,
                    )
                )

            for symbol in self._rsi_symbols:
                if not self._can_open_more():
                    break
                key = (symbol, "rsi2")
                if key in self._positions:
                    continue
                df = symbol_data.get(symbol)
                if df is None:
                    continue
                signal = df.iloc[-2]
                bar_time = int(signal["time"])
                action_key = (bar_time, "entry", symbol, "rsi2")
                if action_key in self._processed_actions:
                    continue

                rsi2 = signal.get("rsi2")
                trend_ma = signal.get("trend_ma")
                low_5d = signal.get("low_5d")
                atr_value = signal.get("rsi_atr")
                if pd.isna(rsi2) or pd.isna(trend_ma) or pd.isna(low_5d) or pd.isna(atr_value) or float(atr_value) <= 0:
                    continue

                rsi_oversold = float(rsi2) < self._rsi_entry
                in_uptrend = float(signal["close"]) > float(trend_ma)
                capitulation = float(signal["low"]) <= float(low_5d)
                base_ok = bullish and rsi_oversold and in_uptrend
                if not base_ok:
                    continue
                if self._rsi_require_capitulation and not capitulation:
                    continue

                actual_entry = _apply_entry_slippage(float(signal["close"]), self._slippage_rate)
                hard_stop = actual_entry - self._rsi_atr_stop_mult * float(atr_value)
                if hard_stop <= 0 or hard_stop >= actual_entry:
                    continue
                risk_budget = self._entry_risk_budget()
                qty = risk_budget / (actual_entry - hard_stop)
                position_ratio = (actual_entry * qty) / max(self._balance, 1.0)
                opportunities.append(
                    Opportunity(
                        strategy_name=self.name,
                        title=f"Enter {symbol} on RSI-2 mean reversion",
                        money_involved=risk_budget,
                        data={
                            "action": "entry",
                            "strategy_kind": "rsi2",
                            "symbol": symbol,
                            "entry": actual_entry,
                            "qty": qty,
                            "hard_stop": hard_stop,
                            "risk_budget": risk_budget,
                            "position_ratio": position_ratio,
                            "bar_time": bar_time,
                            "bar_time_iso": signal["time_dt"].isoformat(),
                        },
                        pre_score=0.8,
                    )
                )

            # Track last entry time and store dry-spell state for async notifier.
            entry_opps = [o for o in opportunities if o.data.get("action") == "entry"]
            if entry_opps:
                self._last_entry_ts = time.monotonic()
                self._last_dry_spell_notify = 0.0  # reset so next dry spell notifies promptly
            else:
                now = time.monotonic()
                hours_since_notify = (now - self._last_dry_spell_notify) / 3600
                if hours_since_notify >= self._dry_spell_notify_hours:
                    hours_since_entry = (now - self._last_entry_ts) / 3600 if self._last_entry_ts else None
                    self._pending_dry_spell = {
                        "bullish": bullish,
                        "btc_close": round(float(btc_df.iloc[-2]["close"]), 0),
                        "btc_ema": round(float(btc_ema), 0),
                        "hours_since_entry": round(hours_since_entry, 1) if hours_since_entry else None,
                        "open_positions": len(self._positions),
                    }
                    self._last_dry_spell_notify = now

            return opportunities
        except Exception:
            log.exception("combined_strategy.scan_failed")
            return []

    def _entry_risk_budget(self) -> float:
        budget = self._balance * self._risk_per_trade
        if self._max_trade_usd > 0:
            budget = min(budget, self._max_trade_usd)
        return max(budget, 1.0)

    def _current_portfolio_risk(self) -> float:
        balance = max(self._balance, 1.0)
        return sum(((pos.entry - pos.hard_stop) * pos.qty) / balance for pos in self._positions.values())

    def _can_open_more(self) -> bool:
        if len(self._positions) >= self._max_open_positions:
            return False
        return self._current_portfolio_risk() + self._risk_per_trade <= self._max_portfolio_risk
