"""FastAPI web dashboard — minimal API for monitoring and control."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

if TYPE_CHECKING:
    from moneyclaw.agent.brain import AgentBrain
    from moneyclaw.agent.memory import Memory
    from moneyclaw.execution.risk import RiskManager
    from moneyclaw.llm.router import LLMRouter
    from moneyclaw.plugins.registry import StrategyRegistry


def create_app(
    brain: AgentBrain,
    memory: Memory,
    llm: LLMRouter,
    strategies: StrategyRegistry,
    risk: RiskManager,
) -> FastAPI:
    """Create the FastAPI app with all routes."""
    app = FastAPI(title="MoneyClaw", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        status = await brain.get_status()
        return f"""<!DOCTYPE html>
<html>
<head><title>MoneyClaw</title>
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 text-gray-100 p-8 font-mono">
<h1 class="text-3xl font-bold mb-6">MoneyClaw</h1>
<div class="grid grid-cols-2 gap-4 max-w-2xl">
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Status</div>
    <div class="text-xl">{"Running" if status["running"] else "Stopped"}</div>
  </div>
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Today P&L</div>
    <div class="text-xl">${status["today_pnl"]:.2f}</div>
  </div>
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Active Strategies</div>
    <div class="text-xl">{status["strategies_active"]}</div>
  </div>
  <div class="bg-gray-800 p-4 rounded">
    <div class="text-sm text-gray-400">Pending Approvals</div>
    <div class="text-xl">{status["pending_approvals"]}</div>
  </div>
</div>
<div class="mt-6 bg-gray-800 p-4 rounded max-w-2xl">
  <div class="text-sm text-gray-400 mb-2">LLM Cost</div>
  <pre class="text-sm">{status["llm_cost"]}</pre>
</div>
<div class="mt-4 text-sm text-gray-500">
  <a href="/api/status" class="underline">API</a> |
  <a href="/api/strategies" class="underline">Strategies</a> |
  <a href="/api/history" class="underline">History</a>
</div>
</body></html>"""

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

    @app.post("/api/pause")
    async def api_pause() -> dict:
        risk.pause()
        return {"status": "paused"}

    @app.post("/api/resume")
    async def api_resume() -> dict:
        risk.resume()
        return {"status": "resumed"}

    return app
