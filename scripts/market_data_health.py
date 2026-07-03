from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.regime_detector import detect_regimes


def build_health_report(project_root: Path) -> dict:
    regimes = detect_regimes(project_root)
    symbols = regimes.get("symbols", [])

    degraded = [s for s in symbols if s.get("liquidity_quality") == "degraded"]
    unknown = [s for s in symbols if s.get("liquidity_quality") == "unknown"]
    fresh = [s for s in symbols if s.get("liquidity_quality") == "good"]

    max_staleness = max((s.get("staleness_minutes") or 0.0) for s in symbols) if symbols else None
    avg_staleness = (
        round(sum((s.get("staleness_minutes") or 0.0) for s in symbols) / len(symbols), 2)
        if symbols else None
    )

    status = "healthy"
    if degraded:
        status = "degraded"
    elif unknown:
        status = "warning"

    recommendations: list[str] = []
    if degraded:
        recommendations.append("refresh or restart market data collection before trusting strategy tuning")
    if max_staleness and max_staleness > 180:
        recommendations.append("treat regime analysis as stale until quote collection resumes")
    if not recommendations:
        recommendations.append("market data looks usable for strategy analysis")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "market_db": regimes.get("market_db"),
        "symbol_count": len(symbols),
        "fresh_symbols": [s.get("symbol") for s in fresh],
        "degraded_symbols": [s.get("symbol") for s in degraded],
        "unknown_symbols": [s.get("symbol") for s in unknown],
        "avg_staleness_minutes": avg_staleness,
        "max_staleness_minutes": max_staleness,
        "recommendations": recommendations,
        "symbols": symbols,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check MoneyClaw market data freshness and feed health.")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", help="Optional JSON output path.")
    args = parser.parse_args()

    payload = build_health_report(Path(args.project_root))
    rendered = json.dumps(payload, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
