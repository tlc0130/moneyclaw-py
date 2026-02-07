"""FastAPI web dashboard — HTMX-powered monitoring and control."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).parent / "static"

if TYPE_CHECKING:
    from moneyclaw.agent.brain import AgentBrain
    from moneyclaw.agent.memory import Memory
    from moneyclaw.execution.risk import RiskManager
    from moneyclaw.execution.trading import TradeExecutor
    from moneyclaw.llm.router import LLMRouter
    from moneyclaw.plugins.registry import StrategyRegistry


def create_app(
    brain: AgentBrain,
    memory: Memory,
    llm: LLMRouter,
    strategies: StrategyRegistry,
    risk: RiskManager,
    executor: TradeExecutor | None = None,
) -> FastAPI:
    """Create the FastAPI app with all routes."""
    app = FastAPI(title="MoneyClaw", version="0.1.0")
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        status = await brain.get_status()
        strats = strategies.status()
        history = await memory.get_history(limit=10)
        pending = await memory.get_pending()

        strat_rows = ""
        for s in strats:
            icon = "🟢" if s["enabled"] else "⚪"
            strat_rows += (
                f'<tr class="border-b border-gray-700">'
                f'<td class="py-2 px-3">{icon}</td>'
                f'<td class="py-2 px-3">{s["name"]}</td>'
                f'<td class="py-2 px-3">{s["risk_level"]}</td>'
                f'<td class="py-2 px-3">{s["roi_estimate"]:.1f}x</td>'
                f'<td class="py-2 px-3">{s["description"]}</td>'
                f"</tr>"
            )

        history_rows = ""
        for h in history:
            pnl = h["profit_loss"]
            color = "text-green-400" if pnl >= 0 else "text-red-400"
            sign = "+" if pnl >= 0 else ""
            ts = datetime.fromtimestamp(h["executed_at"], tz=UTC).strftime("%m-%d %H:%M")
            dry = ""
            if h.get("details") and h["details"].get("dry_run"):
                dry = ' <span class="text-yellow-500 text-xs">DRY</span>'
            history_rows += (
                f'<tr class="border-b border-gray-700">'
                f'<td class="py-2 px-3 text-gray-400">{ts}</td>'
                f'<td class="py-2 px-3">{h["strategy"]}</td>'
                f'<td class="py-2 px-3">{h["title"][:50]}</td>'
                f'<td class="py-2 px-3 {color}">{sign}${pnl:.2f}{dry}</td>'
                f"</tr>"
            )

        pending_rows = ""
        for p in pending:
            ts = datetime.fromtimestamp(p["created_at"], tz=UTC).strftime("%m-%d %H:%M")
            pending_rows += (
                f'<tr class="border-b border-gray-700">'
                f'<td class="py-2 px-3 text-gray-400">{ts}</td>'
                f'<td class="py-2 px-3">{p["strategy"]}</td>'
                f'<td class="py-2 px-3">{p["title"][:50]}</td>'
                f'<td class="py-2 px-3">'
                f'<button hx-post="/api/approve/{p["id"]}" hx-swap="outerHTML" '
                f'class="bg-green-700 px-2 py-1 rounded text-xs mr-1">Approve</button>'
                f'<button hx-post="/api/reject/{p["id"]}" hx-swap="outerHTML" '
                f'class="bg-red-700 px-2 py-1 rounded text-xs">Reject</button>'
                f"</td>"
                f"</tr>"
            )

        risk_status = status.get("risk", risk.status())
        pnl_val = status["today_pnl"]
        pnl_color = "text-green-400" if pnl_val >= 0 else "text-red-400"
        pnl_sign = "+" if pnl_val >= 0 else ""
        dry_badge = (
            '<span class="bg-yellow-600 text-xs px-2 py-1 rounded ml-2">DRY RUN</span>'
            if status.get("dry_run")
            else ""
        )

        order_count = len(executor.order_history) if executor else 0

        return f"""<!DOCTYPE html>
<html>
<head>
<title>MoneyClaw Dashboard</title>
<meta charset="utf-8">
<script src="/static/htmx.min.js"></script>
<script src="https://cdn.tailwindcss.com"></script><!-- Tailwind CDN (JIT) -->
</head>
<body class="bg-gray-900 text-gray-100 font-mono">
<div class="max-w-5xl mx-auto p-6">

<div class="flex items-center justify-between mb-6">
  <h1 class="text-3xl font-bold">MoneyClaw{dry_badge}</h1>
  <div class="text-sm text-gray-400">Tick #{status["tick_count"]}</div>
</div>

<!-- Status Cards -->
<div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6"
     hx-get="/htmx/cards" hx-trigger="every 30s" hx-swap="innerHTML">
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Status</div>
    <div class="text-xl">{"🟢 Running" if status["running"] else "🔴 Stopped"}</div>
  </div>
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Today P&L</div>
    <div class="text-xl {pnl_color}">{pnl_sign}${pnl_val:.2f}</div>
  </div>
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Strategies</div>
    <div class="text-xl">{status["strategies_active"]} active</div>
  </div>
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Orders</div>
    <div class="text-xl">{order_count}</div>
  </div>
</div>

<!-- Risk Status -->
<div class="bg-gray-800 p-4 rounded mb-6">
  <div class="flex items-center justify-between mb-2">
    <div class="text-sm text-gray-400">Risk Controls</div>
    <div class="space-x-2">
      <button hx-post="/api/pause" hx-swap="none" class="bg-red-700 px-3 py-1 rounded text-xs">Pause</button>
      <button hx-post="/api/resume" hx-swap="none" class="bg-green-700 px-3 py-1 rounded text-xs">Resume</button>
    </div>
  </div>
  <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
    <div>Daily loss: ${risk_status["daily_loss"]:.2f} / ${risk_status["daily_loss_limit"]:.2f}</div>
    <div>Consec. losses: {risk_status["consecutive_losses"]} / {
            risk_status["cooldown_threshold"]
        }</div>
    <div>Max position: {risk_status["max_position_ratio"]:.0%}</div>
    <div>Paused: {"Yes" if risk_status["paused"] else "No"}</div>
  </div>
</div>

<!-- LLM Cost -->
<div class="bg-gray-800 p-4 rounded mb-6">
  <div class="text-sm text-gray-400 mb-2">LLM Cost</div>
  <pre class="text-sm">{status["llm_cost"]}</pre>
</div>

<!-- Strategies -->
<div class="bg-gray-800 p-4 rounded mb-6">
  <div class="text-sm text-gray-400 mb-2">Strategies</div>
  <table class="w-full text-sm">
    <thead><tr class="text-gray-400 border-b border-gray-600">
      <th class="py-2 px-3 text-left w-8"></th>
      <th class="py-2 px-3 text-left">Name</th>
      <th class="py-2 px-3 text-left">Risk</th>
      <th class="py-2 px-3 text-left">ROI</th>
      <th class="py-2 px-3 text-left">Description</th>
    </tr></thead>
    <tbody>{
            strat_rows
            or '<tr><td colspan="5" class="py-4 text-center text-gray-500">No strategies loaded</td></tr>'
        }</tbody>
  </table>
</div>

<!-- Pending Approvals -->
{
            ""
            if not pending_rows
            else f'''
<div class="bg-gray-800 p-4 rounded mb-6">
  <div class="text-sm text-gray-400 mb-2">Pending Approvals ({len(pending)})</div>
  <table class="w-full text-sm">
    <thead><tr class="text-gray-400 border-b border-gray-600">
      <th class="py-2 px-3 text-left">Time</th>
      <th class="py-2 px-3 text-left">Strategy</th>
      <th class="py-2 px-3 text-left">Title</th>
      <th class="py-2 px-3 text-left">Action</th>
    </tr></thead>
    <tbody>{pending_rows}</tbody>
  </table>
</div>
'''
        }

<!-- Trade History -->
<div class="bg-gray-800 p-4 rounded mb-6"
     hx-get="/htmx/history" hx-trigger="every 60s" hx-swap="innerHTML">
  <div class="text-sm text-gray-400 mb-2">Recent Trades</div>
  <table class="w-full text-sm">
    <thead><tr class="text-gray-400 border-b border-gray-600">
      <th class="py-2 px-3 text-left">Time</th>
      <th class="py-2 px-3 text-left">Strategy</th>
      <th class="py-2 px-3 text-left">Title</th>
      <th class="py-2 px-3 text-left">P&L</th>
    </tr></thead>
    <tbody>{
            history_rows
            or '<tr><td colspan="4" class="py-4 text-center text-gray-500">No trades yet</td></tr>'
        }</tbody>
  </table>
</div>

<div class="text-sm text-gray-500">
  <a href="/api/status" class="underline">API</a> |
  <a href="/api/strategies" class="underline">Strategies</a> |
  <a href="/api/history" class="underline">History</a> |
  <a href="/api/orders" class="underline">Orders</a>
</div>

</div>
</body></html>"""

    # --- HTMX partials ---

    @app.get("/htmx/cards", response_class=HTMLResponse)
    async def htmx_cards() -> str:
        status = await brain.get_status()
        pnl_val = status["today_pnl"]
        pnl_color = "text-green-400" if pnl_val >= 0 else "text-red-400"
        pnl_sign = "+" if pnl_val >= 0 else ""
        order_count = len(executor.order_history) if executor else 0
        return f"""
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Status</div>
    <div class="text-xl">{"🟢 Running" if status["running"] else "🔴 Stopped"}</div>
  </div>
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Today P&L</div>
    <div class="text-xl {pnl_color}">{pnl_sign}${pnl_val:.2f}</div>
  </div>
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Strategies</div>
    <div class="text-xl">{status["strategies_active"]} active</div>
  </div>
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Orders</div>
    <div class="text-xl">{order_count}</div>
  </div>"""

    @app.get("/htmx/history", response_class=HTMLResponse)
    async def htmx_history() -> str:
        history = await memory.get_history(limit=10)
        rows = ""
        for h in history:
            pnl = h["profit_loss"]
            color = "text-green-400" if pnl >= 0 else "text-red-400"
            sign = "+" if pnl >= 0 else ""
            ts = datetime.fromtimestamp(h["executed_at"], tz=UTC).strftime("%m-%d %H:%M")
            rows += (
                f'<tr class="border-b border-gray-700">'
                f'<td class="py-2 px-3 text-gray-400">{ts}</td>'
                f'<td class="py-2 px-3">{h["strategy"]}</td>'
                f'<td class="py-2 px-3">{h["title"][:50]}</td>'
                f'<td class="py-2 px-3 {color}">{sign}${pnl:.2f}</td>'
                f"</tr>"
            )
        return f"""
  <div class="text-sm text-gray-400 mb-2">Recent Trades</div>
  <table class="w-full text-sm">
    <thead><tr class="text-gray-400 border-b border-gray-600">
      <th class="py-2 px-3 text-left">Time</th>
      <th class="py-2 px-3 text-left">Strategy</th>
      <th class="py-2 px-3 text-left">Title</th>
      <th class="py-2 px-3 text-left">P&L</th>
    </tr></thead>
    <tbody>{rows or '<tr><td colspan="4" class="py-4 text-center text-gray-500">No trades yet</td></tr>'}</tbody>
  </table>"""

    # --- JSON APIs ---

    @app.get("/api/status")
    async def api_status() -> dict:
        return await brain.get_status()

    @app.get("/api/strategies")
    async def api_strategies() -> list[dict]:
        return strategies.status()

    @app.get("/api/history")
    async def api_history() -> list[dict]:
        return await memory.get_history()

    @app.get("/api/cost")
    async def api_cost() -> dict:
        summary = llm.cost_tracker.get_daily_summary()
        return {
            "today_cost": llm.cost_tracker.today_cost,
            "today_calls": llm.cost_tracker.today_calls,
            "total_cost": llm.cost_tracker.get_total_cost(),
            "over_budget": llm.cost_tracker.is_over_budget(),
        } | (
            {
                "by_layer": summary.cost_by_layer,
                "calls_by_layer": summary.calls_by_layer,
            }
            if summary
            else {}
        )

    @app.get("/api/risk")
    async def api_risk() -> dict:
        return risk.status()

    @app.get("/api/orders")
    async def api_orders() -> list[dict]:
        if not executor:
            return []
        return [
            {
                "id": o.id,
                "exchange": o.exchange,
                "symbol": o.symbol,
                "side": o.side,
                "type": o.type,
                "amount": o.amount,
                "price": o.price,
                "filled": o.filled,
                "status": o.status,
                "dry_run": o.dry_run,
                "timestamp": o.timestamp.isoformat(),
            }
            for o in executor.order_history
        ]

    @app.get("/api/pending")
    async def api_pending() -> list[dict]:
        return await memory.get_pending()

    @app.post("/api/pause")
    async def api_pause() -> dict:
        risk.pause()
        return {"status": "paused"}

    @app.post("/api/resume")
    async def api_resume() -> dict:
        risk.resume()
        return {"status": "resumed"}

    @app.post("/api/approve/{opp_id}")
    async def api_approve(opp_id: str) -> HTMLResponse:
        ok = await memory.approve(opp_id)
        if ok:
            return HTMLResponse('<span class="text-green-400">Approved</span>')
        return HTMLResponse('<span class="text-red-400">Not found</span>')

    @app.post("/api/reject/{opp_id}")
    async def api_reject(opp_id: str) -> HTMLResponse:
        ok = await memory.reject(opp_id)
        if ok:
            return HTMLResponse('<span class="text-gray-400">Rejected</span>')
        return HTMLResponse('<span class="text-red-400">Not found</span>')

    return app
