"""Diagnose why LLM health checks fail on this host — prints the REAL error
(the registry logs it at debug level, which is hidden at INFO).

Run from the repo root on the VPS:
    ./venv/bin/python llm_check.py
"""

import asyncio
import os
from pathlib import Path

# Load .env into the environment so litellm sees OPENAI_API_KEY / ANTHROPIC_API_KEY
# (systemd sets these for the service, but a manual run does not).
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from moneyclaw.cli import _force_ipv4

_force_ipv4()

from litellm import acompletion


async def test(model: str) -> None:
    try:
        r = await acompletion(
            model=model, messages=[{"role": "user", "content": "ping"}], max_tokens=5
        )
        print(f"OK   {model} -> choices={bool(r.choices)}")
    except Exception as e:  # noqa: BLE001 - surface the real error
        print(f"FAIL {model} -> {type(e).__name__}: {str(e)[:400]}")


async def main() -> None:
    for m in ("openai/gpt-4o", "anthropic/claude-sonnet-4-5-20250929"):
        await test(m)


if __name__ == "__main__":
    asyncio.run(main())
