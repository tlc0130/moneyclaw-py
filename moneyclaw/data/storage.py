"""Time-series market data storage backed by DuckDB."""

from __future__ import annotations

from pathlib import Path
from typing import List

import structlog

from .feeds.base import OHLCV, Quote

log = structlog.get_logger()


class MarketStorage:
    """Persist quotes and OHLCV bars to a DuckDB file."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn = None
        self._init_db()

    def _init_db(self) -> None:
        try:
            import duckdb

            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(self._path)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quotes (
                    symbol  VARCHAR,
                    price   DOUBLE,
                    volume  DOUBLE,
                    ts      TIMESTAMP DEFAULT current_timestamp
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ohlcv (
                    symbol  VARCHAR,
                    open    DOUBLE,
                    high    DOUBLE,
                    low     DOUBLE,
                    close   DOUBLE,
                    volume  DOUBLE,
                    ts      TIMESTAMP DEFAULT current_timestamp
                )
                """
            )
            log.info("market_storage.opened", path=self._path)
        except ImportError:
            log.warning("market_storage.duckdb_missing", path=self._path)
        except Exception:
            log.exception("market_storage.init_failed", path=self._path)

    def store_quotes(self, quotes: List[Quote]) -> None:
        if not self._conn or not quotes:
            return
        try:
            rows = [(q.symbol, q.price, q.volume) for q in quotes]
            self._conn.executemany(
                "INSERT INTO quotes (symbol, price, volume) VALUES (?, ?, ?)", rows
            )
        except Exception:
            log.exception("market_storage.store_quotes_failed")

    def store_ohlcv(self, bars: List[OHLCV]) -> None:
        if not self._conn or not bars:
            return
        try:
            rows = [(b.symbol, b.open, b.high, b.low, b.close, b.volume) for b in bars]
            self._conn.executemany(
                "INSERT INTO ohlcv (symbol, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        except Exception:
            log.exception("market_storage.store_ohlcv_failed")

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
