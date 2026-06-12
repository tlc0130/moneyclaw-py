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

CREATE TABLE IF NOT EXISTS paper_positions (
    symbol TEXT PRIMARY KEY,
    quantity REAL NOT NULL DEFAULT 0,
    avg_entry REAL NOT NULL DEFAULT 0,
    last_price REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    notional REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    details TEXT
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
            "INSERT OR REPLACE INTO opportunities"
            " (id, strategy, title, data, score, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (opp.id, opp.strategy_name, opp.title, json.dumps(opp.data), 0.0, now, now),
        )
        await self._db.commit()

    async def record_result(self, opp: Opportunity, result: Result) -> None:
        assert self._db
        now = time.time()

        await self._db.execute(
            "INSERT OR IGNORE INTO opportunities"
            " (id, strategy, title, data, score, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'executed', ?, ?)",
            (opp.id, opp.strategy_name, opp.title, json.dumps(opp.data), 0.0, now, now),
        )

        # Update opportunity status
        await self._db.execute(
            "UPDATE opportunities SET strategy = ?, title = ?, data = ?, status = 'executed', updated_at = ? WHERE id = ?",
            (opp.strategy_name, opp.title, json.dumps(opp.data), now, opp.id),
        )

        # Insert result
        await self._db.execute(
            "INSERT INTO results (opportunity_id, profit_loss, details, executed_at)"
            " VALUES (?, ?, ?, ?)",
            (opp.id, result.profit_loss, json.dumps(result.details), now),
        )

        # Update daily P&L
        today = date.today().isoformat()
        if result.profit_loss >= 0:
            await self._db.execute(
                "INSERT INTO daily_pnl (date, total_profit, trade_count) VALUES (?, ?, 1) "
                "ON CONFLICT(date) DO UPDATE SET"
                " total_profit = total_profit + ?, trade_count = trade_count + 1",
                (today, result.profit_loss, result.profit_loss),
            )
        else:
            await self._db.execute(
                "INSERT INTO daily_pnl (date, total_loss, trade_count) VALUES (?, ?, 1) "
                "ON CONFLICT(date) DO UPDATE SET"
                " total_loss = total_loss + ?, trade_count = trade_count + 1",
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

    async def today_trade_count(self) -> int:
        """Trades executed *today* (mirrors today_pnl). The daily report used to
        show len(get_history(limit=20)), an all-time query capped at 20 — so it
        read "20" on any day with >=20 historical trades, even with zero today."""
        assert self._db
        today = date.today().isoformat()
        async with self._db.execute(
            "SELECT trade_count FROM daily_pnl WHERE date = ?", (today,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

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
            "SELECT id, strategy, title, data, created_at FROM opportunities"
            " WHERE status = 'pending' ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "strategy": r[1],
                    "title": r[2],
                    "data": json.loads(r[3]),
                    "created_at": r[4],
                }
                for r in rows
            ]

    async def approve(self, opp_id: str) -> bool:
        assert self._db
        result = await self._db.execute(
            "UPDATE opportunities SET status = 'approved', updated_at = ?"
            " WHERE id = ? AND status = 'pending'",
            (time.time(), opp_id),
        )
        await self._db.commit()
        return result.rowcount > 0

    async def reject(self, opp_id: str) -> bool:
        assert self._db
        result = await self._db.execute(
            "UPDATE opportunities SET status = 'rejected', updated_at = ?"
            " WHERE id = ? AND status = 'pending'",
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

    async def get_strategy_history(self, strategy_name: str, limit: int = 50) -> list[dict]:
        """Get execution history for a specific strategy."""
        assert self._db
        async with self._db.execute(
            "SELECT r.executed_at, o.title, r.profit_loss, r.details "
            "FROM results r JOIN opportunities o ON r.opportunity_id = o.id "
            "WHERE o.strategy = ? "
            "ORDER BY r.executed_at DESC LIMIT ?",
            (strategy_name, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "executed_at": r[0],
                    "title": r[1],
                    "profit_loss": r[2],
                    "details": json.loads(r[3]) if r[3] else None,
                }
                for r in rows
            ]

    async def get_strategy_stats(self, strategy_name: str) -> dict:
        """Get execution statistics for a specific strategy."""
        assert self._db
        # Get total executions and success rate
        async with self._db.execute(
            "SELECT COUNT(*), AVG(r.profit_loss), SUM(CASE WHEN r.profit_loss > 0 THEN 1 ELSE 0 END) "
            "FROM results r JOIN opportunities o ON r.opportunity_id = o.id "
            "WHERE o.strategy = ?",
            (strategy_name,),
        ) as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0
            avg_pnl = row[1] if row and row[1] else 0.0
            profitable = row[2] if row else 0

        # Get last 24h stats
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).timestamp()
        async with self._db.execute(
            "SELECT COUNT(*), SUM(r.profit_loss) "
            "FROM results r JOIN opportunities o ON r.opportunity_id = o.id "
            "WHERE o.strategy = ? AND r.executed_at > ?",
            (strategy_name, yesterday),
        ) as cursor:
            row = await cursor.fetchone()
            recent_count = row[0] if row else 0
            recent_pnl = row[1] if row and row[1] else 0.0

        return {
            "total_executions": total,
            "profitable_trades": profitable,
            "success_rate": (profitable / total * 100) if total > 0 else 0.0,
            "avg_pnl": avg_pnl,
            "recent_executions": recent_count,
            "recent_pnl": recent_pnl,
        }

    async def paper_buy(
        self,
        symbol: str,
        quantity: float,
        price: float,
        details: dict | None = None,
    ) -> None:
        assert self._db
        now = time.time()
        async with self._db.execute(
            "SELECT quantity, avg_entry FROM paper_positions WHERE symbol = ?", (symbol,)
        ) as cursor:
            row = await cursor.fetchone()

        current_qty = float(row[0]) if row else 0.0
        current_avg = float(row[1]) if row else 0.0
        new_qty = current_qty + quantity
        new_avg = ((current_qty * current_avg) + (quantity * price)) / new_qty if new_qty > 0 else 0.0

        await self._db.execute(
            "INSERT INTO paper_positions (symbol, quantity, avg_entry, last_price, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET quantity = excluded.quantity, avg_entry = excluded.avg_entry, "
            "last_price = excluded.last_price, updated_at = excluded.updated_at",
            (symbol, new_qty, new_avg, price, now),
        )
        await self._db.execute(
            "INSERT INTO paper_ledger (symbol, side, quantity, price, notional, realized_pnl, created_at, details) VALUES (?, 'buy', ?, ?, ?, 0, ?, ?)",
            (symbol, quantity, price, quantity * price, now, json.dumps(details or {})),
        )
        await self._db.commit()

    async def paper_mark_price(self, symbol: str, price: float) -> None:
        assert self._db
        await self._db.execute(
            "UPDATE paper_positions SET last_price = ?, updated_at = ? WHERE symbol = ?",
            (price, time.time(), symbol),
        )
        await self._db.commit()

    async def get_paper_portfolio(self) -> dict:
        assert self._db
        async with self._db.execute(
            "SELECT symbol, quantity, avg_entry, last_price FROM paper_positions WHERE quantity > 0"
        ) as cursor:
            rows = await cursor.fetchall()

        positions = []
        total_cost = 0.0
        total_value = 0.0
        for symbol, quantity, avg_entry, last_price in rows:
            quantity = float(quantity)
            avg_entry = float(avg_entry)
            last_price = float(last_price)
            cost = quantity * avg_entry
            value = quantity * last_price
            unrealized = value - cost
            total_cost += cost
            total_value += value
            positions.append(
                {
                    "symbol": symbol,
                    "quantity": quantity,
                    "avg_entry": avg_entry,
                    "last_price": last_price,
                    "cost_basis": cost,
                    "market_value": value,
                    "unrealized_pnl": unrealized,
                }
            )

        async with self._db.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM paper_ledger"
        ) as cursor:
            realized_row = await cursor.fetchone()
            realized_pnl = float(realized_row[0] if realized_row else 0.0)

        return {
            "positions": positions,
            "total_cost_basis": total_cost,
            "total_market_value": total_value,
            "unrealized_pnl": total_value - total_cost,
            "realized_pnl": realized_pnl,
            "total_pnl": realized_pnl + (total_value - total_cost),
        }
