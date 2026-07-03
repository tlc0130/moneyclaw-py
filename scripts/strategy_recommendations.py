from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.market_data_health import build_health_report
from scripts.strategy_intel_snapshot import build_snapshot


def generate_recommendations(project_root: Path, target_strategy: str = "combined_crypto_strategy") -> dict:
    snapshot = build_snapshot(project_root, target_strategy=target_strategy)
    health = build_health_report(project_root)

    recs: list[dict] = []

    if health["status"] == "degraded":
        recs.append(
            {
                "type": "operational_blocker",
                "priority": "critical",
                "target": "market_data_feed",
                "action": "restore fresh quote collection before making strategy adjustments",
                "reason": "regime detection is currently based on stale quotes",
                "evidence": {
                    "degraded_symbols": health["degraded_symbols"],
                    "max_staleness_minutes": health["max_staleness_minutes"],
                },
                "expected_effect": "prevents tuning decisions from being driven by outdated market context",
                "confidence": 0.98,
                "requires_shadow_test": False,
            }
        )

    runtime_probe = snapshot.get("strategy_runtime_probe", {})
    if runtime_probe.get("status") != "ok":
        recs.append(
            {
                "type": "runtime_blocker",
                "priority": "critical",
                "target": target_strategy,
                "action": "fix exchange/API connectivity for the live generated strategy before expecting new opportunities or execution history",
                "reason": "the runtime probe for the live strategy cannot fetch its baseline Binance market data",
                "evidence": {
                    "strategy_module_path": runtime_probe.get("strategy_module_path"),
                    "probe_symbol": runtime_probe.get("probe_symbol"),
                    "error_type": runtime_probe.get("error_type"),
                    "error": runtime_probe.get("error"),
                },
                "expected_effect": "restores the strategy's ability to scan, generate opportunities, and populate SQLite history",
                "confidence": 0.97,
                "requires_shadow_test": False,
            }
        )

    observed = {item["strategy"]: item for item in snapshot.get("observed_strategies", [])}
    if target_strategy not in observed:
        recs.append(
            {
                "type": "observability_gap",
                "priority": "high",
                "target": target_strategy,
                "action": "instrument the live generated strategy so executions and result details are tagged under its active strategy name",
                "reason": "the live strategy appears in CLI output and its source is now resolved, but it still does not appear in historical SQLite stats",
                "evidence": {
                    "observed_strategies": list(observed.keys()),
                    "strategy_cli_excerpt": snapshot.get("strategy_cli", {}).get("stdout", "")[:400],
                    "strategy_module_path": snapshot.get("strategy_source", {}).get("module_path"),
                    "configured_strategies_dir": snapshot.get("strategy_source", {}).get("configured_strategies_dir"),
                },
                "expected_effect": "enables evidence-based tuning for the actual live strategy instead of older static strategies",
                "confidence": 0.95,
                "requires_shadow_test": False,
            }
        )

    for symbol in snapshot.get("market_regimes", {}).get("symbols", []):
        if symbol.get("trend") == "uptrend" and symbol.get("breakout_environment") == "clean" and symbol.get("liquidity_quality") != "good":
            recs.append(
                {
                    "type": "risk_control",
                    "priority": "medium",
                    "target": target_strategy,
                    "action": f"when feed health returns, consider validating Donchian/trend component first for {symbol['symbol']} while keeping execution size conservative until data stability is confirmed",
                    "reason": "market regime looks trend-supportive, but stale quotes make direct parameter promotion unsafe",
                    "evidence": {
                        "symbol": symbol["symbol"],
                        "trend": symbol["trend"],
                        "breakout_environment": symbol["breakout_environment"],
                        "liquidity_quality": symbol["liquidity_quality"],
                    },
                    "expected_effect": "preserves the backtested edge thesis while avoiding overconfidence under degraded data",
                    "confidence": 0.74,
                    "requires_shadow_test": True,
                }
            )

    if not recs:
        recs.append(
            {
                "type": "no_change",
                "priority": "low",
                "target": target_strategy,
                "action": "keep current settings and continue collecting evidence",
                "reason": "no strong tuning signal detected",
                "evidence": {},
                "expected_effect": "avoids unnecessary strategy churn",
                "confidence": 0.6,
                "requires_shadow_test": False,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_strategy": target_strategy,
        "summary": {
            "dashboard_reachable": snapshot.get("dashboard", {}).get("reachable"),
            "market_data_status": health.get("status"),
            "observed_historical_strategies": [item["strategy"] for item in snapshot.get("observed_strategies", [])],
            "recommendation_count": len(recs),
        },
        "recommendations": recs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate MoneyClaw strategy recommendation packets.")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--target-strategy", default="combined_crypto_strategy")
    parser.add_argument("--output", help="Optional JSON output path.")
    args = parser.parse_args()

    payload = generate_recommendations(Path(args.project_root), target_strategy=args.target_strategy)
    rendered = json.dumps(payload, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
