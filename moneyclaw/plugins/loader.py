"""Plugin loader — discovers and loads strategy plugins from the strategies directory."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import structlog

from moneyclaw.plugins.base import Strategy

log = structlog.get_logger()


def discover_strategies(strategies_dir: str | Path) -> list[type[Strategy]]:
    """Discover Strategy subclasses from Python files in the strategies directory."""
    path = Path(strategies_dir)
    if not path.exists():
        log.warning("plugins.dir_missing", path=str(path))
        return []

    found: list[type[Strategy]] = []

    for strategy_path in path.iterdir():
        # Each strategy is a directory with a __init__.py, or a single .py file
        if strategy_path.is_dir():
            init = strategy_path / "__init__.py"
            if not init.exists():
                continue
            module_file = init
            module_name = f"strategies.{strategy_path.name}"
        elif strategy_path.suffix == ".py" and strategy_path.name != "__init__.py":
            module_file = strategy_path
            module_name = f"strategies.{strategy_path.stem}"
        else:
            continue

        try:
            spec = importlib.util.spec_from_file_location(module_name, module_file)
            if spec is None or spec.loader is None:
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Find Strategy subclasses
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, Strategy) and attr is not Strategy:
                    found.append(attr)
                    log.info("plugins.discovered", strategy=attr.name, module=module_name)

        except Exception:
            log.exception("plugins.load_error", path=str(module_file))

    return found
