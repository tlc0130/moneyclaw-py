"""FastAPI web dashboard — HTMX-powered monitoring and control."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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

    # --- Template Setup ---
    _TEMPLATE_DIR = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    # --- API Endpoints (JSON) ---

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
    async def api_approve(opp_id: str) -> dict:
        ok = await memory.approve(opp_id)
        return {"status": "approved" if ok else "not_found"}

    @app.post("/api/reject/{opp_id}")
    async def api_reject(opp_id: str) -> dict:
        ok = await memory.reject(opp_id)
        return {"status": "rejected" if ok else "not_found"}

    # --- Chat API ---
    from pydantic import BaseModel

    class ChatRequest(BaseModel):
        message: str

    @app.post("/api/chat")
    async def api_chat(payload: ChatRequest) -> dict:
        """Simple chat interface. In future, this will connect to the LLM agent."""
        msg = payload.message.lower()
        
        # Simple command parsing for demo
        if "status" in msg:
            return {"response": f"System is currently {'RUNNING' if brain.is_running else 'STOPPED'}. P&L: ${await memory.today_pnl():.2f}"}
        elif "list strategies" in msg:
            names = [s.name for s in strategies.active]
            return {"response": f"Active strategies: {', '.join(names)}"}
        elif "risk" in msg:
            r = risk.status()
            return {"response": f"Risk Level: LOW. Daily Loss: ${r['daily_loss']:.2f}"}
        
        return {"response": f"Command received: '{payload.message}'. I am monitoring the markets."}

    # --- HTMX Partials (Legacy/Fallback) ---
    # Keeping minimal HTMX partials if needed, or removing them if fully switching to JS.
    # For now, let's keep the API endpoints clean and assume the JS frontend uses them.

    return app
