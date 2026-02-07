"""Persistent memory via SQLite — the agent remembers everything."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import aiosqlite
import structlog

from moneyclaw.plugins.base import Opportunity, Result

log = structlog.get_logger()

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    strategy TEXT NOT NULL,
    title TEXT NOT NULL,
    data TEXT NOT NULL,
    score REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
    profit_loss REAL NOT NULL,
    details TEXT,
    executed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    date TEXT PRIMARY KEY,
    total_profit REAL NOT NULL DEFAULT 0,
    total_loss REAL NOT NULL DEFAULT 0,
    trade_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities(status);
CREATE INDEX IF NOT EXISTS idx_opp_strategy ON opportunities(strategy);
CREATE INDEX IF NOT EXISTS idx_results_date ON results(executed_at);
"""


class Memory:
    """Agent's persistent memory. Tracks opportunities, results, and P&L."""

    def __init__(self, db_path: str | Path = "data/moneyclaw.db") -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        log.info("memory.initialized", path=str(self._db_path))

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def record_pending(self, opp: Opportunity) -> None:
        assert self._db
        now = time.time()
        await self._db.execute(
            "INSERT OR REPLACE INTO opportunities (id, strategy, title, data, score, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (opp.id, opp.strategy_name, opp.title, json.dumps(opp.data), 0.0, now, now),
        )
        await self._db.commit()

    async def record_result(self, opp: Opportunity, result: Result) -> None:
        assert self._db
        now = time.time()

        # Update opportunity status
        await self._db.execute(
            "UPDATE opportunities SET status = 'executed', updated_at = ? WHERE id = ?",
            (now, opp.id),
        )

        # Insert result
        await self._db.execute(
            "INSERT INTO results (opportunity_id, profit_loss, details, executed_at) VALUES (?, ?, ?, ?)",
            (opp.id, result.profit_loss, json.dumps(result.details), now),
        )

        # Update daily P&L
        today = date.today().isoformat()
        if result.profit_loss >= 0:
            await self._db.execute(
                "INSERT INTO daily_pnl (date, total_profit, trade_count) VALUES (?, ?, 1) "
                "ON CONFLICT(date) DO UPDATE SET total_profit = total_profit + ?, trade_count = trade_count + 1",
                (today, result.profit_loss, result.profit_loss),
            )
        else:
            await self._db.execute(
                "INSERT INTO daily_pnl (date, total_loss, trade_count) VALUES (?, ?, 1) "
                "ON CONFLICT(date) DO UPDATE SET total_loss = total_loss + ?, trade_count = trade_count + 1",
                (today, abs(result.profit_loss), abs(result.profit_loss)),
            )

        await self._db.commit()

    async def today_pnl(self) -> float:
        assert self._db
        today = date.today().isoformat()
        async with self._db.execute(
            "SELECT total_profit - total_loss FROM daily_pnl WHERE date = ?", (today,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0.0

    async def pending_count(self) -> int:
        assert self._db
        async with self._db.execute(
            "SELECT COUNT(*) FROM opportunities WHERE status = 'pending'"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_pending(self) -> list[dict]:
        assert self._db
        async with self._db.execute(
            "SELECT id, strategy, title, data, created_at FROM opportunities WHERE status = 'pending' ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {"id": r[0], "strategy": r[1], "title": r[2], "data": json.loads(r[3]), "created_at": r[4]}
                for r in rows
            ]

    async def approve(self, opp_id: str) -> bool:
        assert self._db
        result = await self._db.execute(
            "UPDATE opportunities SET status = 'approved', updated_at = ? WHERE id = ? AND status = 'pending'",
            (time.time(), opp_id),
        )
        await self._db.commit()
        return result.rowcount > 0

    async def reject(self, opp_id: str) -> bool:
        assert self._db
        result = await self._db.execute(
            "UPDATE opportunities SET status = 'rejected', updated_at = ? WHERE id = ? AND status = 'pending'",
            (time.time(), opp_id),
        )
        await self._db.commit()
        return result.rowcount > 0

    async def get_history(self, limit: int = 50) -> list[dict]:
        assert self._db
        async with self._db.execute(
            "SELECT r.executed_at, o.strategy, o.title, r.profit_loss, r.details "
            "FROM results r JOIN opportunities o ON r.opportunity_id = o.id "
            "ORDER BY r.executed_at DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "executed_at": r[0],
                    "strategy": r[1],
                    "title": r[2],
                    "profit_loss": r[3],
                    "details": json.loads(r[4]) if r[4] else None,
                }
                for r in rows
            ]
