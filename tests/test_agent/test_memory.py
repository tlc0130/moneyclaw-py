"""Tests for agent memory (SQLite persistence)."""

from __future__ import annotations

import pytest

from moneyclaw.agent.memory import Memory
from moneyclaw.plugins.base import Opportunity, Result


@pytest.fixture
async def memory(tmp_path):
    mem = Memory(db_path=tmp_path / "test.db")
    await mem.init()
    yield mem
    await mem.close()


class TestMemory:
    @pytest.mark.asyncio
    async def test_record_and_retrieve_pending(self, memory: Memory) -> None:
        opp = Opportunity(
            id="test1",
            strategy_name="test_strategy",
            title="Test opportunity",
            money_involved=100,
        )
        await memory.record_pending(opp)
        pending = await memory.get_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == "test1"

    @pytest.mark.asyncio
    async def test_approve_removes_from_pending(self, memory: Memory) -> None:
        opp = Opportunity(id="test2", strategy_name="test", title="Test")
        await memory.record_pending(opp)
        assert await memory.pending_count() == 1

        success = await memory.approve("test2")
        assert success is True
        assert await memory.pending_count() == 0

    @pytest.mark.asyncio
    async def test_reject(self, memory: Memory) -> None:
        opp = Opportunity(id="test3", strategy_name="test", title="Test")
        await memory.record_pending(opp)

        success = await memory.reject("test3")
        assert success is True
        assert await memory.pending_count() == 0

    @pytest.mark.asyncio
    async def test_record_result_updates_pnl(self, memory: Memory) -> None:
        opp = Opportunity(id="test4", strategy_name="test", title="Test")
        await memory.record_pending(opp)

        result = Result(success=True, profit_loss=5.0, details={"note": "test"})
        await memory.record_result(opp, result)

        pnl = await memory.today_pnl()
        assert pnl == 5.0

    @pytest.mark.asyncio
    async def test_history(self, memory: Memory) -> None:
        opp = Opportunity(id="test5", strategy_name="test", title="Made money")
        await memory.record_pending(opp)
        await memory.record_result(opp, Result(profit_loss=10.0))

        history = await memory.get_history()
        assert len(history) == 1
        assert history[0]["profit_loss"] == 10.0
