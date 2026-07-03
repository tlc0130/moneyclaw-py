"""In-memory log event buffer and analyzer.

LogBuffer is installed as a structlog processor early in the pipeline.
LogAnalyzer consumes the buffer and produces a structured summary for
the StrategyTuner LLM prompt.
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Any

_MAX_EVENTS = 3000  # ~24-48 h at 60 s scan intervals with moderate logging


class LogBuffer:
    """Thread-safe ring buffer that shadows every structlog event.

    Register as a processor BEFORE the final renderer so event_dict is still
    a plain dict (not yet serialized).  The processor returns the dict unchanged
    so the normal render chain continues unaffected.
    """

    def __init__(self) -> None:
        self._events: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=_MAX_EVENTS
        )
        self._lock = threading.Lock()

    # ── structlog processor protocol ──────────────────────────────────────────

    def __call__(self, logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        event_dict.setdefault("_ts", time.time())
        with self._lock:
            self._events.append(dict(event_dict))
        return event_dict

    # ── query helpers ─────────────────────────────────────────────────────────

    def recent(self, seconds: float = 86_400.0) -> list[dict[str, Any]]:
        cutoff = time.time() - seconds
        with self._lock:
            return [e for e in self._events if e.get("_ts", 0) >= cutoff]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


# ── Analyzer ──────────────────────────────────────────────────────────────────

# Events we track by name
_RISK_BLOCK_EVENTS = frozenset({
    "risk.trade_too_large",
    "risk.daily_loss_limit",
    "risk.cooldown",
    "risk.position_too_large",
    "risk.strategy_daily_limit",
    "risk.paused",
})

_ERROR_EVENTS = frozenset({
    "combined_strategy.scan_failed",
    "combined_strategy.symbol_fetch_failed",
    "combined_strategy.balance_refresh_failed",
    "combined_strategy.position_unprotected",
    "combined_strategy.sell_failed_stop_replaced",
    "combined_strategy.state_load_error",
    "trade.error",
    "trade.stop_error",
    "agent.tick_error",
    "agent.scan_error",
    "agent.execute_error",
})


class LogAnalyzer:
    """Converts raw log events into a structured summary dict."""

    def __init__(self, buffer: LogBuffer) -> None:
        self._buffer = buffer

    def summarize(self, window_seconds: float = 86_400.0) -> dict[str, Any]:
        events = self._buffer.recent(window_seconds)

        scan_opp_counts: list[int] = []
        trade_entries: list[dict] = []
        trade_exits: list[dict] = []
        risk_blocks: list[dict] = []
        stops_fired: list[dict] = []
        errors: list[dict] = []
        symbols_unavailable: list[str] = []

        for e in events:
            ev = e.get("event", "")

            if ev == "agent.opportunities_found":
                scan_opp_counts.append(int(e.get("count", 0)))

            elif ev == "agent.executed":
                side = "entry" if "Enter" in str(e.get("strategy", "") + str(e)) else "exit"
                record = {
                    "strategy": e.get("strategy", ""),
                    "profit": e.get("profit", 0.0),
                    "success": e.get("success", False),
                    "dry_run": e.get("dry_run", True),
                    "ts": e.get("_ts"),
                }
                if "exit" in str(e.get("strategy", "")):
                    trade_exits.append(record)
                else:
                    trade_entries.append(record)

            elif ev in _RISK_BLOCK_EVENTS:
                risk_blocks.append({
                    "reason": ev,
                    "amount": e.get("amount"),
                    "ratio": e.get("ratio"),
                    "ts": e.get("_ts"),
                })

            elif ev == "combined_strategy.stopped_out":
                stops_fired.append({
                    "symbol": e.get("symbol"),
                    "hard_stop": e.get("hard_stop"),
                    "ts": e.get("_ts"),
                })

            elif ev in _ERROR_EVENTS:
                errors.append({
                    "event": ev,
                    "symbol": e.get("symbol"),
                    "error": str(e.get("error", ""))[:120],
                    "ts": e.get("_ts"),
                })

            elif ev == "combined_strategy.symbols_unavailable":
                dropped = e.get("dropped", [])
                if isinstance(dropped, list):
                    symbols_unavailable.extend(str(s) for s in dropped)

        total_scans = len(scan_opp_counts)
        scans_with_signals = sum(1 for c in scan_opp_counts if c > 0)

        return {
            "window_hours": round(window_seconds / 3600, 1),
            "total_log_events": len(events),
            "scans_total": total_scans,
            "scans_with_signals": scans_with_signals,
            "scans_without_signals": total_scans - scans_with_signals,
            "avg_opps_per_scan": (
                round(sum(scan_opp_counts) / total_scans, 2) if total_scans else 0
            ),
            "trade_entries": len(trade_entries),
            "trade_exits": len(trade_exits),
            "stops_fired": len(stops_fired),
            "stopped_symbols": [s["symbol"] for s in stops_fired],
            "risk_blocks": len(risk_blocks),
            "risk_block_reasons": _count_by(risk_blocks, "reason"),
            "errors": len(errors),
            "error_types": _count_by(errors, "event"),
            "symbols_unavailable": list(set(symbols_unavailable)),
        }


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        v = str(item.get(key, "unknown"))
        counts[v] = counts.get(v, 0) + 1
    return counts
