from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import yaml

from moneyclaw.config.settings import Settings
from scripts.regime_detector import detect_regimes
from scripts.strategy_runtime_probe import run_probe


class TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._inside_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag.lower() == "title":
            self._inside_title = True

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag.lower() == "title":
            self._inside_title = False

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._inside_title:
            self.title += data


def _check_dashboard(url: str) -> dict:
    try:
        with urlopen(url, timeout=5) as response:
            html = response.read().decode("utf-8", errors="ignore")
            parser = TitleParser()
            parser.feed(html)
            return {
                "reachable": True,
                "status": getattr(response, "status", 200),
                "title": parser.title.strip() or None,
            }
    except URLError as exc:
        return {"reachable": False, "status": None, "title": None, "error": str(exc)}


def _load_recent_results(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    rows = conn.execute(
        """
        SELECT r.executed_at, o.strategy, o.title, r.profit_loss, r.details
        FROM results r
        JOIN opportunities o ON o.id = r.opportunity_id
        WHERE r.executed_at >= ?
        ORDER BY r.executed_at DESC
        """,
        (since,),
    ).fetchall()
    results = []
    for executed_at, strategy, title, profit_loss, details in rows:
        results.append(
            {
                "executed_at": datetime.fromtimestamp(executed_at, timezone.utc).isoformat(),
                "strategy": strategy,
                "title": title,
                "profit_loss": profit_loss,
                "details": details,
            }
        )
    return results


def _collect_strategy_stats(conn: sqlite3.Connection) -> tuple[list[dict], list[str]]:
    strategy_rows = conn.execute(
        "SELECT strategy, COUNT(*) AS cnt FROM opportunities GROUP BY strategy ORDER BY cnt DESC"
    ).fetchall()
    strategy_names = [row[0] for row in strategy_rows]
    payload: list[dict] = []
    for strategy in strategy_names:
        total_exec, avg_pnl, profitable = conn.execute(
            """
            SELECT COUNT(*), COALESCE(AVG(r.profit_loss), 0), COALESCE(SUM(CASE WHEN r.profit_loss > 0 THEN 1 ELSE 0 END), 0)
            FROM results r
            JOIN opportunities o ON o.id = r.opportunity_id
            WHERE o.strategy = ?
            """,
            (strategy,),
        ).fetchone()
        recent_exec, recent_pnl = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(r.profit_loss), 0)
            FROM results r
            JOIN opportunities o ON o.id = r.opportunity_id
            WHERE o.strategy = ? AND r.executed_at >= ?
            """,
            (strategy, (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()),
        ).fetchone()
        payload.append(
            {
                "strategy": strategy,
                "observed_opportunities": next((row[1] for row in strategy_rows if row[0] == strategy), 0),
                "total_executions": total_exec,
                "avg_pnl": round(avg_pnl or 0.0, 4),
                "success_rate_pct": round(((profitable / total_exec) * 100.0) if total_exec else 0.0, 2),
                "recent_executions_24h": recent_exec,
                "recent_pnl_24h": round(recent_pnl or 0.0, 4),
            }
        )
    return payload, strategy_names


def _fetch_daily_pnl(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(total_profit - total_loss, 0) FROM daily_pnl WHERE date = ?",
        (datetime.now(timezone.utc).date().isoformat(),),
    ).fetchone()
    return float(row[0] if row else 0.0)


def _fetch_pending_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM opportunities WHERE status = 'pending'").fetchone()
    return int(row[0] if row else 0)


def _recent_titles(results: list[dict]) -> list[str]:
    counter = Counter(item["title"] for item in results if item.get("title"))
    return [title for title, _count in counter.most_common(5)]


def _run_moneyclaw_strategies(project_root: Path) -> dict:
    # Use the interpreter running this script — works for any venv layout
    # (Linux venv/bin, Windows .venv/Scripts) and under systemd.
    python_exe = Path(sys.executable)
    try:
        proc = subprocess.run(
            [str(python_exe), "-m", "moneyclaw", "strategies"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def _inspect_strategy_source(strategies_dir: Path, target_strategy: str) -> dict:
    if not strategies_dir.exists():
        return {"configured_strategies_dir": str(strategies_dir), "exists": False}

    candidates = list(strategies_dir.glob("*.py")) + list(strategies_dir.glob("*/__init__.py"))
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if f'name = "{target_strategy}"' not in text and f"name = '{target_strategy}'" not in text:
            continue
        config_path = candidate.with_name("config.yaml") if candidate.name == "__init__.py" else candidate.parent / "config.yaml"
        config = None
        if config_path.exists():
            try:
                config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            except Exception:
                config = None
        return {
            "configured_strategies_dir": str(strategies_dir),
            "exists": True,
            "module_path": str(candidate),
            "config_path": str(config_path) if config_path.exists() else None,
            "config": config,
        }

    return {
        "configured_strategies_dir": str(strategies_dir),
        "exists": True,
        "module_path": None,
        "config_path": None,
        "config": None,
    }


def build_snapshot(project_root: Path, target_strategy: str = "combined_crypto_strategy") -> dict:
    settings = Settings(_env_file=project_root / ".env")
    db_path = project_root / settings.db_path
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        strategy_stats, observed_strategy_names = _collect_strategy_stats(conn)
        recent_results = _load_recent_results(conn, days=30)
        dashboard = _check_dashboard(f"http://127.0.0.1:{settings.web_port}")
        regime_payload = detect_regimes(project_root)
        strategy_cli = _run_moneyclaw_strategies(project_root)
        strategy_source = _inspect_strategy_source(Path(settings.strategies_dir), target_strategy)
        strategy_runtime_probe = run_probe(project_root, target_strategy=target_strategy)

        notes: list[str] = []
        if target_strategy not in observed_strategy_names:
            notes.append(
                f"target strategy '{target_strategy}' is not present in historical SQLite records; current runtime may be using a newer/generated strategy path"
            )
        if strategy_cli.get("ok") and target_strategy not in strategy_cli.get("stdout", ""):
            notes.append("CLI strategy listing does not currently mention the target strategy")
        if strategy_source.get("module_path"):
            notes.append(f"live strategy source resolved at {strategy_source['module_path']}")
        else:
            notes.append("configured strategies_dir does not currently expose a source file for the target strategy")
        if strategy_runtime_probe.get("status") != "ok":
            notes.append(
                f"runtime probe failed for {strategy_runtime_probe.get('probe_symbol')}: {strategy_runtime_probe.get('error_type')} - {strategy_runtime_probe.get('error')}"
            )
        if not dashboard.get("reachable"):
            notes.append("dashboard is not reachable")

        stale_symbols = [
            symbol["symbol"]
            for symbol in regime_payload.get("symbols", [])
            if symbol.get("liquidity_quality") == "degraded"
        ]
        if stale_symbols:
            notes.append(f"stale/degraded market data for: {', '.join(stale_symbols)}")

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(project_root),
            "db_path": str(db_path),
            "target_strategy": target_strategy,
            "dashboard": dashboard,
            "moneyclaw_status": {
                "today_pnl": round(_fetch_daily_pnl(conn), 4),
                "pending_approvals": _fetch_pending_count(conn),
            },
            "strategy_cli": strategy_cli,
            "strategy_source": strategy_source,
            "strategy_runtime_probe": strategy_runtime_probe,
            "observed_strategies": strategy_stats,
            "recent_results_30d_count": len(recent_results),
            "recent_titles": _recent_titles(recent_results),
            "market_regimes": regime_payload,
            "notes": notes,
        }
    finally:
        conn.close()


def render_markdown(snapshot: dict) -> str:
    lines = [
        "# Daily Strategy Intelligence Report",
        "",
        f"Date: {snapshot['generated_at'][:10]}",
        f"Target strategy: `{snapshot['target_strategy']}`",
        "",
        "## Executive summary",
        f"- Dashboard reachable: {'yes' if snapshot['dashboard'].get('reachable') else 'no'}",
        f"- Today P&L: {snapshot['moneyclaw_status']['today_pnl']:+.2f}",
        f"- Pending approvals: {snapshot['moneyclaw_status']['pending_approvals']}",
        f"- Observed historical strategies: {', '.join(s['strategy'] for s in snapshot['observed_strategies']) or 'none'}",
        "",
        "## Dashboard",
        f"- URL: http://127.0.0.1:8080",
        f"- Title: {snapshot['dashboard'].get('title') or 'unknown'}",
        "",
        "## Observed strategy stats",
        "| Strategy | Opportunities | Executions | Avg P&L | Success % | Execs 24h | P&L 24h |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in snapshot["observed_strategies"]:
        lines.append(
            f"| {item['strategy']} | {item['observed_opportunities']} | {item['total_executions']} | {item['avg_pnl']:.4f} | {item['success_rate_pct']:.2f} | {item['recent_executions_24h']} | {item['recent_pnl_24h']:.4f} |"
        )

    lines.extend([
        "",
        "## Market regimes",
        "| Symbol | Trend | Volatility | Breakout | Liquidity | Risk | Confidence | Notes |",
        "|---|---|---|---|---|---|---:|---|",
    ])
    for symbol in snapshot["market_regimes"].get("symbols", []):
        lines.append(
            f"| {symbol['symbol']} | {symbol['trend']} | {symbol['volatility']} | {symbol['breakout_environment']} | {symbol['liquidity_quality']} | {symbol['risk_posture']} | {symbol['confidence']:.2f} | {'; '.join(symbol['notes'])} |"
        )

    lines.extend([
        "",
        "## Recent activity themes",
    ])
    for title in snapshot.get("recent_titles", []):
        lines.append(f"- {title}")
    if not snapshot.get("recent_titles"):
        lines.append("- none observed in recent results")

    lines.extend([
        "",
        "## Notes",
    ])
    for note in snapshot.get("notes", []):
        lines.append(f"- {note}")
    if not snapshot.get("notes"):
        lines.append("- no major concerns detected by the snapshot script")

    lines.extend([
        "",
        "## Raw strategy CLI output",
        "```text",
        snapshot.get("strategy_cli", {}).get("stdout", "").strip() or "(no output)",
        "```",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a MoneyClaw strategy intelligence snapshot.")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--target-strategy", default="combined_crypto_strategy")
    parser.add_argument("--json-output", help="Optional JSON output path.")
    parser.add_argument("--markdown-output", help="Optional Markdown output path.")
    args = parser.parse_args()

    project_root = Path(args.project_root)
    snapshot = build_snapshot(project_root, target_strategy=args.target_strategy)
    rendered_json = json.dumps(snapshot, indent=2)
    rendered_md = render_markdown(snapshot)

    if args.json_output:
        json_path = Path(args.json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(rendered_json, encoding="utf-8")
    if args.markdown_output:
        md_path = Path(args.markdown_output)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(rendered_md, encoding="utf-8")

    print(rendered_json)


if __name__ == "__main__":
    main()
