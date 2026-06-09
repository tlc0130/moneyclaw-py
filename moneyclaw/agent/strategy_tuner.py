"""Strategy Tuner — daily LLM-driven parameter optimization.

Flow (runs once per day via the scheduler):
  1. Summarize recent log events (LogAnalyzer)
  2. Pull recent P&L + trade history from Memory
  3. Fetch crypto news headlines (feedparser RSS)
  4. Fetch BTC market regime from exchange
  5. Send all context to LLM → structured JSON recommendations
  6. Validate changes against per-parameter bounds in config.yaml
  7. Atomically rewrite config.yaml with approved changes
  8. Notify via Telegram
  9. Record the change + rationale in data/tuner_log.jsonl
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import yaml

if TYPE_CHECKING:
    from moneyclaw.agent.log_analyzer import LogAnalyzer
    from moneyclaw.agent.memory import Memory
    from moneyclaw.interface.telegram.notify import Notifier
    from moneyclaw.llm.router import LLMRouter
    from moneyclaw.plugins.registry import StrategyRegistry

log = structlog.get_logger()

_TUNER_LOG = Path("data/tuner_log.jsonl")

# Crypto news RSS feeds (no API key required)
_NEWS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
]
_NEWS_TIMEOUT = 8.0   # seconds per feed
_MAX_HEADLINES = 12


# ── Bounds ─────────────────────────────────────────────────────────────────────

# Hard-coded safety limits — config.yaml bounds can only TIGHTEN these, not loosen.
_ABSOLUTE_BOUNDS: dict[str, tuple[float, float]] = {
    "rsi2.entry":            (3.0,  30.0),
    "rsi2.exit":             (50.0, 90.0),
    "rsi2.atr_stop_mult":    (1.0,  5.0),
    "rsi2.time_stop_days":   (5,    20),
    "rsi2.day5_green_check": (3,    10),
    "donchian.entry_channel": (10,  120),
    "donchian.exit_channel":  (5,   40),
    "donchian.atr_stop_mult": (1.0, 4.0),
    "common.risk_per_trade":  (0.003, 0.04),
    "common.max_open_positions": (1, 8),
}


class StrategyTuner:
    """Runs periodic LLM analysis and applies safe config adjustments."""

    def __init__(
        self,
        *,
        config_path: Path,
        log_analyzer: LogAnalyzer,
        memory: Memory,
        llm: LLMRouter,
        notifier: Notifier | None = None,
        strategies: StrategyRegistry | None = None,
        min_change_interval_hours: float = 24.0,
        min_trades_for_tuning: int = 0,
    ) -> None:
        self._config_path = config_path
        self._log_analyzer = log_analyzer
        self._memory = memory
        self._llm = llm
        self._notifier = notifier
        self._strategies = strategies
        self._min_change_interval = min_change_interval_hours * 3600
        self._min_trades = min_trades_for_tuning
        self._last_run: float = 0.0

    # ── Public API ─────────────────────────────────────────────────────────────

    async def maybe_run(self) -> None:
        """Run analysis only if the cooldown has elapsed."""
        if time.time() - self._last_run < self._min_change_interval:
            return
        await self.run_analysis()

    async def run_analysis(self) -> None:
        """Full analysis-and-tune cycle."""
        self._last_run = time.time()
        log.info("tuner.starting")

        try:
            log_summary = self._log_analyzer.summarize(window_seconds=86_400.0)
            trade_history = await self._memory.get_history(limit=50)
            today_pnl = await self._memory.today_pnl()
            news_headlines = await self._fetch_news()
            market_ctx = self._fetch_market_context()
            current_cfg = self._load_config()
            bounds = self._effective_bounds(current_cfg)

            prompt = self._build_prompt(
                log_summary=log_summary,
                trade_history=trade_history,
                today_pnl=today_pnl,
                news_headlines=news_headlines,
                market_ctx=market_ctx,
                current_cfg=current_cfg,
                bounds=bounds,
            )

            response_text = await self._call_llm(prompt)
            changes, analysis, reasoning = self._parse_response(response_text)

            validated = self._validate(changes, bounds, current_cfg)
            if validated:
                self._apply_changes(validated, current_cfg)
                self._hot_reload_strategies()
                await self._notify(validated, analysis, reasoning, today_pnl)
                self._record(validated, analysis, reasoning, log_summary)
                log.info("tuner.changes_applied", params=list(validated.keys()))
            else:
                log.info("tuner.no_changes", analysis=analysis[:200])
                self._record({}, analysis, reasoning, log_summary)

        except Exception:
            log.exception("tuner.run_failed")

    # ── Context gathering ──────────────────────────────────────────────────────

    async def _fetch_news(self) -> list[str]:
        """Fetch recent crypto headlines from RSS feeds."""
        import asyncio
        import feedparser  # bundled dependency

        headlines: list[str] = []

        async def _fetch_one(url: str) -> list[str]:
            loop = asyncio.get_running_loop()
            try:
                feed = await asyncio.wait_for(
                    loop.run_in_executor(None, feedparser.parse, url),
                    timeout=_NEWS_TIMEOUT,
                )
                return [
                    entry.get("title", "")
                    for entry in (feed.entries or [])[:8]
                    if entry.get("title")
                ]
            except Exception:
                return []

        results = await asyncio.gather(*[_fetch_one(u) for u in _NEWS_FEEDS])
        for r in results:
            headlines.extend(r)
        return headlines[:_MAX_HEADLINES]

    def _fetch_market_context(self) -> dict[str, Any]:
        """Fetch BTC recent price data for market regime context."""
        try:
            import ccxt

            ex = ccxt.binanceus({"enableRateLimit": True, "timeout": 10_000})
            candles = ex.fetch_ohlcv("BTC/USDT", "1d", limit=10)
            if len(candles) < 2:
                return {"available": False}
            prices = [c[4] for c in candles]
            latest = prices[-1]
            week_ago = prices[0]
            return {
                "available": True,
                "btc_price": round(latest, 0),
                "btc_7d_change_pct": round((latest / week_ago - 1) * 100, 2),
                "btc_trend": "up" if latest > week_ago else "down",
            }
        except Exception:
            return {"available": False}

    # ── Prompt ────────────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        *,
        log_summary: dict,
        trade_history: list[dict],
        today_pnl: float,
        news_headlines: list[str],
        market_ctx: dict,
        current_cfg: dict,
        bounds: dict[str, tuple[float, float]],
    ) -> str:
        recent_trades = trade_history[:10]
        trade_lines = "\n".join(
            f"  {t.get('strategy','?')} | {t.get('title','?')[:40]} | P&L: {t.get('profit_loss',0):+.4f}"
            for t in recent_trades
        ) or "  (no trades yet)"

        news_lines = "\n".join(f"  - {h}" for h in news_headlines) or "  (no headlines fetched)"

        market_lines = (
            f"BTC price: ${market_ctx.get('btc_price', 'N/A')}  "
            f"7-day change: {market_ctx.get('btc_7d_change_pct', 'N/A')}%  "
            f"Trend: {market_ctx.get('btc_trend', 'N/A')}"
            if market_ctx.get("available")
            else "Market data unavailable"
        )

        bounds_lines = "\n".join(
            f"  {k}: min={v[0]}, max={v[1]}, current={self._get_nested(current_cfg, k)}"
            for k, v in sorted(bounds.items())
        )

        return f"""You are an automated trading strategy optimizer for a Donchian breakout + RSI-2 mean-reversion crypto strategy.

## Log Summary (last 24 hours)
- Total scans: {log_summary.get('scans_total', 0)}
- Scans with signals: {log_summary.get('scans_with_signals', 0)} / {log_summary.get('scans_total', 0)}
- Trade entries attempted: {log_summary.get('trade_entries', 0)}
- Trade exits attempted: {log_summary.get('trade_exits', 0)}
- Stop-losses fired: {log_summary.get('stops_fired', 0)} (symbols: {log_summary.get('stopped_symbols', [])})
- Risk blocks: {log_summary.get('risk_blocks', 0)} ({log_summary.get('risk_block_reasons', {})})
- Errors: {log_summary.get('errors', 0)} ({log_summary.get('error_types', {})})
- Unavailable symbols: {log_summary.get('symbols_unavailable', [])}

## Recent Trade History (last 10)
{trade_lines}

## Today's P&L
${today_pnl:+.4f}

## Market Context
{market_lines}

## Recent News Headlines
{news_lines}

## Current Configuration Parameters (tunable only)
{bounds_lines}

## Task
Analyze the data above and suggest parameter adjustments that would improve performance.

Guidelines:
- If 0 signals in 24h: consider loosening RSI entry threshold or reducing Donchian channel length
- If many stops firing: consider wider ATR stop multiplier or reduce risk_per_trade
- If no signals AND market trending up: BTC regime filter may be blocking — note this (but don't change it)
- If risk blocks are frequent: the current sizing may be too large
- Only change parameters that are clearly supported by the data
- Do NOT change parameters if there is insufficient data (fewer than 3 trades)
- Small, incremental changes only (stay within bounds)

Respond with ONLY valid JSON in this exact format (no markdown, no explanation outside JSON):
{{
  "analysis": "2-3 sentence summary of what the data shows",
  "changes": {{
    "rsi2.entry": 12
  }},
  "reasoning": "one sentence per changed parameter explaining why"
}}

Use an empty object {{}} for "changes" if no changes are needed.
The parameter keys must exactly match those listed in Current Configuration Parameters above.
"""

    # ── LLM call ──────────────────────────────────────────────────────────────

    async def _call_llm(self, prompt: str) -> str:
        from moneyclaw.llm.types import LLMLayer, TaskRequest

        request = TaskRequest(
            prompt=prompt,
            system="You are a quantitative trading strategy optimizer. Output valid JSON only.",
            min_layer=LLMLayer.CHEAP,
            max_layer=LLMLayer.PREMIUM,
            complexity=0.7,
            money_involved=0.0,
            temperature=0.1,
            max_tokens=600,
            require_json_mode=False,
        )
        response = await self._llm.complete(request)
        return response.text if hasattr(response, "text") else str(response)

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse_response(self, text: str) -> tuple[dict[str, Any], str, str]:
        """Extract (changes, analysis, reasoning) from LLM text. Returns empty dicts on failure."""
        # Strip markdown code fences
        text = re.sub(r"```(?:json)?", "", text).strip()

        # Try to find a JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            log.warning("tuner.parse_no_json", text=text[:200])
            return {}, "Parse failed: no JSON found", ""

        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as e:
            log.warning("tuner.parse_json_error", error=str(e), text=text[:200])
            return {}, f"Parse failed: {e}", ""

        changes = data.get("changes") or {}
        analysis = str(data.get("analysis", ""))
        reasoning = str(data.get("reasoning", ""))
        return changes, analysis, reasoning

    # ── Validation and application ────────────────────────────────────────────

    def _effective_bounds(self, cfg: dict) -> dict[str, tuple[float, float]]:
        """Merge absolute hard limits with any tighter bounds in config tuner section."""
        bounds = dict(_ABSOLUTE_BOUNDS)
        tuner_cfg = cfg.get("tuner", {}).get("bounds", {})
        for key, cfg_bounds in tuner_cfg.items():
            if key in bounds and isinstance(cfg_bounds, list) and len(cfg_bounds) == 2:
                abs_lo, abs_hi = bounds[key]
                bounds[key] = (
                    max(float(cfg_bounds[0]), abs_lo),
                    min(float(cfg_bounds[1]), abs_hi),
                )
        return bounds

    def _validate(
        self,
        changes: dict[str, Any],
        bounds: dict[str, tuple[float, float]],
        current_cfg: dict,
    ) -> dict[str, Any]:
        """Return only changes that are within bounds and actually differ from current value."""
        validated: dict[str, Any] = {}
        for key, new_val in changes.items():
            if key not in bounds:
                log.warning("tuner.unknown_param", key=key)
                continue
            lo, hi = bounds[key]
            try:
                new_val = float(new_val)
            except (TypeError, ValueError):
                log.warning("tuner.non_numeric_value", key=key, value=new_val)
                continue
            if not (lo <= new_val <= hi):
                log.warning("tuner.out_of_bounds", key=key, value=new_val, lo=lo, hi=hi)
                continue
            current_val = self._get_nested(current_cfg, key)
            if current_val is not None and abs(float(current_val) - new_val) < 1e-9:
                continue  # no actual change
            validated[key] = new_val
        return validated

    def _apply_changes(self, validated: dict[str, Any], cfg: dict) -> None:
        """Write validated changes into cfg and atomically save config.yaml."""
        for key, value in validated.items():
            self._set_nested(cfg, key, value)

        tmp = self._config_path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        tmp.replace(self._config_path)
        log.info("tuner.config_saved", path=str(self._config_path))

    def _hot_reload_strategies(self) -> None:
        """Tell all loaded strategies to re-read config.yaml without restarting."""
        if not self._strategies:
            return
        for strategy in self._strategies.all_strategies().values():
            reload_fn = getattr(strategy, "reload_config", None)
            if callable(reload_fn):
                try:
                    reload_fn()
                except Exception:
                    log.warning("tuner.reload_failed", strategy=getattr(strategy, "name", "?"))

    # ── Notification and audit ─────────────────────────────────────────────────

    async def _notify(
        self,
        changes: dict[str, Any],
        analysis: str,
        reasoning: str,
        today_pnl: float,
    ) -> None:
        if not self._notifier:
            return
        lines = [
            "Strategy Tuner — daily update",
            f"P&L today: ${today_pnl:+.4f}",
            "",
            f"Analysis: {analysis}",
            "",
        ]
        if changes:
            lines.append("Changes applied:")
            for k, v in changes.items():
                lines.append(f"  {k} = {v}")
            lines.append("")
            lines.append(f"Reasoning: {reasoning}")
        else:
            lines.append("No parameter changes — config unchanged.")

        try:
            await self._notifier.send("\n".join(lines))
        except Exception:
            log.warning("tuner.notify_failed")

    def _record(
        self,
        changes: dict[str, Any],
        analysis: str,
        reasoning: str,
        log_summary: dict,
    ) -> None:
        _TUNER_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "analysis": analysis,
            "changes": changes,
            "reasoning": reasoning,
            "log_summary": {
                k: log_summary[k]
                for k in ("scans_total", "scans_with_signals", "trade_entries", "stops_fired", "risk_blocks", "errors")
                if k in log_summary
            },
        }
        with _TUNER_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    # ── Config helpers ────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        try:
            return yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            log.exception("tuner.config_load_failed")
            return {}

    @staticmethod
    def _get_nested(cfg: dict, dotted_key: str) -> Any:
        """Get cfg['rsi2']['entry'] from 'rsi2.entry'."""
        parts = dotted_key.split(".")
        node: Any = cfg
        for p in parts:
            if not isinstance(node, dict):
                return None
            node = node.get(p)
        return node

    @staticmethod
    def _set_nested(cfg: dict, dotted_key: str, value: Any) -> None:
        """Set cfg['rsi2']['entry'] = value via 'rsi2.entry'."""
        parts = dotted_key.split(".")
        node = cfg
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        # Preserve int type for integer-valued parameters
        existing = node.get(parts[-1])
        if isinstance(existing, int):
            node[parts[-1]] = int(round(value))
        else:
            node[parts[-1]] = value
