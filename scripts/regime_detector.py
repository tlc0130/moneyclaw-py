from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev

import duckdb


@dataclass
class RegimeSnapshot:
    symbol: str
    as_of: str
    samples: int
    latest_price: float | None
    latest_timestamp: str | None
    trend: str
    volatility: str
    breakout_environment: str
    liquidity_quality: str
    risk_posture: str
    momentum_quality: str
    confidence: float
    price_change_pct: float | None
    range_position: float | None
    staleness_minutes: float | None
    notes: list[str]


def _pick_market_db(project_root: Path) -> Path:
    runtime_db = project_root / "data" / "market.runtime.duckdb"
    default_db = project_root / "data" / "market.duckdb"
    if runtime_db.exists():
        return runtime_db
    return default_db


def _fetch_symbols(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute("SELECT DISTINCT symbol FROM quotes ORDER BY symbol").fetchall()
    return [str(row[0]) for row in rows]


def _fetch_quote_rows(con: duckdb.DuckDBPyConnection, symbol: str, limit: int) -> list[tuple]:
    return con.execute(
        """
        SELECT timestamp, price, bid, ask, volume, change_24h
        FROM quotes
        WHERE symbol = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        [symbol, limit],
    ).fetchall()


def _classify_trend(price_change_pct: float | None) -> tuple[str, str]:
    if price_change_pct is None:
        return "unknown", "weak"
    if price_change_pct >= 2.5:
        return "uptrend", "strong"
    if price_change_pct <= -2.5:
        return "downtrend", "strong"
    if abs(price_change_pct) >= 0.75:
        return ("uptrend" if price_change_pct > 0 else "downtrend"), "normal"
    return "range", "weak"


def _classify_volatility(prices: list[float]) -> str:
    if len(prices) < 3:
        return "unknown"
    returns = []
    for older, newer in zip(prices[1:], prices[:-1]):
        if older and newer:
            returns.append((newer - older) / older)
    if len(returns) < 2:
        return "unknown"
    sigma = pstdev(returns)
    if sigma >= 0.02:
        return "high"
    if sigma >= 0.008:
        return "medium"
    return "low"


def _classify_breakout(prices: list[float]) -> tuple[str, float | None]:
    if len(prices) < 5:
        return "unknown", None
    latest = prices[0]
    local_max = max(prices)
    local_min = min(prices)
    if math.isclose(local_max, local_min):
        return "crowded", 0.5
    pos = (latest - local_min) / (local_max - local_min)
    if pos >= 0.85 or pos <= 0.15:
        return "clean", pos
    if 0.4 <= pos <= 0.6:
        return "crowded", pos
    return "false-break-prone", pos


def _classify_liquidity(latest_ts: datetime | None, volumes: list[float]) -> tuple[str, float | None, list[str]]:
    notes: list[str] = []
    if latest_ts is None:
        return "unknown", None, ["no timestamp available"]
    now = datetime.now(timezone.utc)
    if latest_ts.tzinfo is None:
        latest_ts = latest_ts.replace(tzinfo=timezone.utc)
    staleness_minutes = max((now - latest_ts).total_seconds() / 60.0, 0.0)
    avg_volume = mean(volumes) if volumes else 0.0

    if staleness_minutes > 240:
        notes.append(f"quote stream stale ({staleness_minutes:.1f}m old)")
        return "degraded", staleness_minutes, notes
    if staleness_minutes > 60:
        notes.append(f"quote stream aging ({staleness_minutes:.1f}m old)")
        return "degraded", staleness_minutes, notes
    if avg_volume <= 0:
        notes.append("volume missing/zero in quote history")
        return "unknown", staleness_minutes, notes
    return "good", staleness_minutes, notes


def _classify_risk_posture(symbol: str, trend: str, volatility: str) -> str:
    if trend == "uptrend" and volatility in {"low", "medium"}:
        return "risk_on"
    if trend == "downtrend" and volatility == "high":
        return "risk_off"
    if symbol.lower() in {"bitcoin", "ethereum", "solana"} and trend == "uptrend":
        return "risk_on"
    if trend == "downtrend":
        return "risk_off"
    return "neutral"


def build_snapshot(con: duckdb.DuckDBPyConnection, symbol: str, lookback: int = 96) -> RegimeSnapshot:
    rows = _fetch_quote_rows(con, symbol, lookback)
    if not rows:
        return RegimeSnapshot(
            symbol=symbol,
            as_of=datetime.now(timezone.utc).isoformat(),
            samples=0,
            latest_price=None,
            latest_timestamp=None,
            trend="unknown",
            volatility="unknown",
            breakout_environment="unknown",
            liquidity_quality="unknown",
            risk_posture="neutral",
            momentum_quality="weak",
            confidence=0.0,
            price_change_pct=None,
            range_position=None,
            staleness_minutes=None,
            notes=["no quote history available"],
        )

    latest_ts, latest_price, *_ = rows[0]
    prices = [float(row[1]) for row in rows if row[1] is not None]
    volumes = [float(row[4]) for row in rows if row[4] is not None]
    oldest_price = prices[-1] if len(prices) >= 2 else None
    price_change_pct = (((prices[0] - oldest_price) / oldest_price) * 100.0) if oldest_price else None
    trend, momentum = _classify_trend(price_change_pct)
    volatility = _classify_volatility(prices)
    breakout_environment, range_position = _classify_breakout(prices)
    liquidity_quality, staleness_minutes, notes = _classify_liquidity(latest_ts, volumes)
    risk_posture = _classify_risk_posture(symbol, trend, volatility)

    confidence = 0.35
    if len(prices) >= 24:
        confidence += 0.2
    if volatility != "unknown":
        confidence += 0.15
    if breakout_environment != "unknown":
        confidence += 0.15
    if liquidity_quality == "good":
        confidence += 0.15
    confidence = min(confidence, 0.95)

    if price_change_pct is not None:
        notes.append(f"{price_change_pct:+.2f}% over sampled window")
    if range_position is not None:
        notes.append(f"range position={range_position:.2f}")

    latest_ts_iso = latest_ts.isoformat() if isinstance(latest_ts, datetime) else str(latest_ts)
    return RegimeSnapshot(
        symbol=symbol,
        as_of=datetime.now(timezone.utc).isoformat(),
        samples=len(prices),
        latest_price=prices[0] if prices else None,
        latest_timestamp=latest_ts_iso,
        trend=trend,
        volatility=volatility,
        breakout_environment=breakout_environment,
        liquidity_quality=liquidity_quality,
        risk_posture=risk_posture,
        momentum_quality=momentum,
        confidence=round(confidence, 2),
        price_change_pct=round(price_change_pct, 3) if price_change_pct is not None else None,
        range_position=round(range_position, 3) if range_position is not None else None,
        staleness_minutes=round(staleness_minutes, 2) if staleness_minutes is not None else None,
        notes=notes,
    )


def detect_regimes(project_root: Path, symbols: list[str] | None = None, lookback: int = 96) -> dict:
    market_db = _pick_market_db(project_root)
    con = duckdb.connect(str(market_db), read_only=True)
    try:
        resolved_symbols = symbols or _fetch_symbols(con)
        snapshots = [build_snapshot(con, symbol, lookback=lookback) for symbol in resolved_symbols]
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "market_db": str(market_db),
            "symbols": [asdict(snapshot) for snapshot in snapshots],
        }
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect market regime snapshots from MoneyClaw market storage.")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--symbols", nargs="*", help="Symbols to analyze. Defaults to all quote symbols.")
    parser.add_argument("--lookback", type=int, default=96, help="Number of most recent quotes to inspect per symbol.")
    parser.add_argument("--output", help="Optional JSON output path.")
    args = parser.parse_args()

    payload = detect_regimes(Path(args.project_root), args.symbols, args.lookback)
    rendered = json.dumps(payload, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
