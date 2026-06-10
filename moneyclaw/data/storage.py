from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb

from moneyclaw.data.feeds.base import OHLCV, Quote


class MarketStorage:
    def __init__(self, db_path: str = ":memory:") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quotes (
                symbol TEXT NOT NULL,
                price DOUBLE NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                bid DOUBLE DEFAULT 0,
                ask DOUBLE DEFAULT 0,
                volume DOUBLE DEFAULT 0,
                change_24h DOUBLE DEFAULT 0
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ohlcv (
                symbol TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume DOUBLE DEFAULT 0
            )
            """
        )

    def close(self) -> None:
        self._conn.close()

    def store_quotes(self, quotes: list[Quote]) -> int:
        if not quotes:
            return 0
        self._conn.executemany(
            "INSERT INTO quotes (symbol, price, timestamp, bid, ask, volume, change_24h) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    q.symbol,
                    q.price,
                    q.timestamp,
                    q.bid,
                    q.ask,
                    q.volume,
                    q.change_24h,
                )
                for q in quotes
            ],
        )
        return len(quotes)

    def query_prices(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Quote]:
        query = "SELECT symbol, price, timestamp, bid, ask, volume, change_24h FROM quotes WHERE symbol = ?"
        params: list[object] = [symbol]
        if start is not None:
            query += " AND timestamp >= ?"
            params.append(start)
        if end is not None:
            query += " AND timestamp <= ?"
            params.append(end)
        query += " ORDER BY timestamp ASC"
        rows = self._conn.execute(query, params).fetchall()
        return [
            Quote(
                symbol=row[0],
                price=float(row[1]),
                timestamp=row[2],
                bid=float(row[3]),
                ask=float(row[4]),
                volume=float(row[5]),
                change_24h=float(row[6]),
            )
            for row in rows
        ]

    def store_ohlcv(self, symbol: str, bars: list[OHLCV]) -> int:
        if not bars:
            return 0
        self._conn.executemany(
            "INSERT INTO ohlcv (symbol, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (symbol, bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume)
                for bar in bars
            ],
        )
        return len(bars)

    def query_ohlcv(self, symbol: str) -> list[OHLCV]:
        rows = self._conn.execute(
            "SELECT timestamp, open, high, low, close, volume FROM ohlcv WHERE symbol = ? ORDER BY timestamp ASC",
            [symbol],
        ).fetchall()
        return [
            OHLCV(
                timestamp=row[0],
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        ]
