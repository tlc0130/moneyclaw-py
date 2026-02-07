"""Tests for Web Dashboard API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from moneyclaw.interface.web.app import create_app


@pytest.fixture
def client() -> TestClient:
    brain = AsyncMock()
    brain.get_status = AsyncMock(
        return_value={
            "running": True,
            "strategies_active": 3,
            "strategies": [],
            "today_pnl": 12.50,
            "llm_cost": "No LLM usage today.",
            "pending_approvals": 0,
            "risk": {
                "paused": False,
                "dry_run": True,
                "daily_loss": 5.0,
                "daily_loss_limit": 100.0,
                "consecutive_losses": 0,
                "cooldown_threshold": 3,
                "strategy_daily_losses": {},
                "max_position_ratio": 0.5,
            },
            "tick_count": 42,
            "dry_run": True,
        }
    )

    memory = AsyncMock()
    memory.get_history = AsyncMock(return_value=[])
    memory.get_pending = AsyncMock(return_value=[])
    memory.approve = AsyncMock(return_value=True)
    memory.reject = AsyncMock(return_value=True)

    llm = MagicMock()
    llm.cost_tracker.today_cost = 0.05
    llm.cost_tracker.today_calls = 10
    llm.cost_tracker.get_total_cost.return_value = 0.50
    llm.cost_tracker.is_over_budget.return_value = False
    llm.cost_tracker.get_daily_summary.return_value = None

    strategies = MagicMock()
    strategies.status.return_value = [
        {
            "name": "crypto_dca",
            "description": "DCA",
            "risk_level": "low",
            "enabled": True,
            "roi_estimate": 1.5,
        },
    ]

    risk = MagicMock()
    risk.status.return_value = {
        "paused": False,
        "dry_run": True,
        "daily_loss": 5.0,
        "daily_loss_limit": 100.0,
        "consecutive_losses": 0,
        "cooldown_threshold": 3,
        "strategy_daily_losses": {},
        "max_position_ratio": 0.5,
    }

    executor = MagicMock()
    executor.order_history = []

    app = create_app(
        brain=brain, memory=memory, llm=llm, strategies=strategies, risk=risk, executor=executor
    )
    return TestClient(app)


class TestDashboard:
    def test_index_returns_html(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "MoneyClaw" in resp.text
        assert "DRY RUN" in resp.text

    def test_api_status(self, client: TestClient) -> None:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["today_pnl"] == 12.50

    def test_api_strategies(self, client: TestClient) -> None:
        resp = client.get("/api/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "crypto_dca"

    def test_api_history(self, client: TestClient) -> None:
        resp = client.get("/api/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_api_risk(self, client: TestClient) -> None:
        resp = client.get("/api/risk")
        assert resp.status_code == 200
        assert "daily_loss" in resp.json()

    def test_api_orders_empty(self, client: TestClient) -> None:
        resp = client.get("/api/orders")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_api_pause(self, client: TestClient) -> None:
        resp = client.post("/api/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    def test_api_resume(self, client: TestClient) -> None:
        resp = client.post("/api/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "resumed"

    def test_api_approve(self, client: TestClient) -> None:
        resp = client.post("/api/approve/abc123")
        assert resp.status_code == 200
        assert "Approved" in resp.text

    def test_api_reject(self, client: TestClient) -> None:
        resp = client.post("/api/reject/abc123")
        assert resp.status_code == 200
        assert "Rejected" in resp.text

    def test_api_pending(self, client: TestClient) -> None:
        resp = client.get("/api/pending")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_htmx_cards(self, client: TestClient) -> None:
        resp = client.get("/htmx/cards")
        assert resp.status_code == 200
        assert "Running" in resp.text

    def test_htmx_history(self, client: TestClient) -> None:
        resp = client.get("/htmx/history")
        assert resp.status_code == 200
        assert "No trades yet" in resp.text
