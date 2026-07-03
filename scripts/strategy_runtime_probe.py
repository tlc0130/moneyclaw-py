from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from moneyclaw.config.settings import Settings


def _load_strategy_module(strategies_dir: Path, module_name: str):
    py_file = strategies_dir / f"{module_name}.py"
    pkg_init = strategies_dir / module_name / "__init__.py"
    target = py_file if py_file.exists() else pkg_init
    if not target.exists():
        raise FileNotFoundError(f"strategy module not found: {target}")
    full_name = f"strategies.{module_name}"
    spec = importlib.util.spec_from_file_location(full_name, target)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {full_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module, target


def run_probe(project_root: Path, target_strategy: str = "combined_crypto_strategy", module_name: str = "paper_crypto_strategies") -> dict:
    settings = Settings(_env_file=project_root / ".env")
    strategies_dir = Path(settings.strategies_dir)
    module, module_path = _load_strategy_module(strategies_dir, module_name)

    strategy_cls = None
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if isinstance(attr, type) and getattr(attr, "name", None) == target_strategy:
            strategy_cls = attr
            break
    if strategy_cls is None:
        raise RuntimeError(f"strategy class for {target_strategy} not found in {module_path}")

    strategy = strategy_cls()
    probe_symbol = "BTC/USDT"
    timeframe = getattr(strategy, "_timeframe", "1d")
    exchange = getattr(strategy, "_exchange")
    exchange.timeout = min(int(getattr(exchange, "timeout", 30000)), 10000)

    try:
        rows = exchange.fetch_ohlcv(probe_symbol, timeframe, limit=5)
        status = "ok"
        result = {
            "probe_symbol": probe_symbol,
            "timeframe": timeframe,
            "row_count": len(rows),
            "last_row": rows[-1] if rows else None,
        }
    except Exception as exc:
        status = "error"
        result = {
            "probe_symbol": probe_symbol,
            "timeframe": timeframe,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "target_strategy": target_strategy,
        "module_name": module_name,
        "strategy_module_path": str(module_path),
        "strategies_dir": str(strategies_dir),
        **result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe generated MoneyClaw strategy runtime connectivity.")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--target-strategy", default="combined_crypto_strategy")
    parser.add_argument("--module-name", default="paper_crypto_strategies")
    parser.add_argument("--output", help="Optional JSON output path.")
    args = parser.parse_args()

    payload = run_probe(Path(args.project_root), args.target_strategy, args.module_name)
    rendered = json.dumps(payload, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
