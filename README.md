# MoneyClaw

7x24 AI Agent that saves and makes money autonomously.

## What it does

MoneyClaw is an always-on AI agent focused on one thing: **helping you save money and make money**. It runs 24/7, scanning for opportunities, executing strategies, and reporting results — all while keeping its own operating costs near zero.

### LLM Four-Layer Cost Architecture

| Layer | Tech | Cost | Use Cases |
|-------|------|------|-----------|
| 0 | Rules engine | $0 | Price alerts, thresholds, cron jobs, simple math |
| 1 | Ollama (local) | ~$0/day | News summaries, sentiment, filtering, daily chat |
| 2 | DeepSeek/Groq | <$0.50/day | Complex analysis, strategy optimization, reports |
| 3 | Claude/GPT-4 | On-demand | Critical decisions, new strategies, risk assessment |

Target: **< $20/month** total LLM cost.

## Quick Start

```bash
# Clone
git clone https://github.com/moneyclaw/moneyclaw.git
cd moneyclaw

# Setup
cp .env.example .env
# Edit .env with your API keys

# Run with Docker (recommended)
docker compose up -d

# Or run locally
pip install -e ".[dev]"
moneyclaw run
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Current state + today's P&L |
| `/report` | Detailed report (daily/weekly/monthly) |
| `/approve <id>` | Approve a pending operation |
| `/reject <id>` | Reject a pending operation |
| `/pause` | Pause all strategies |
| `/resume` | Resume operations |
| `/cost` | Agent's own running cost |
| `/strategies` | Strategy performance overview |
| `/ask <question>` | Ask the agent anything |

## Writing Strategies

```python
from moneyclaw.plugins.base import Strategy, Opportunity, Score, Result

class MyStrategy(Strategy):
    name = "my_strategy"
    description = "What this strategy does"
    risk_level = "low"
    min_llm_layer = 0

    async def scan(self) -> list[Opportunity]:
        # Scan for opportunities
        ...

    async def evaluate(self, opp: Opportunity) -> Score:
        # Evaluate opportunity value
        ...

    async def execute(self, opp: Opportunity) -> Result:
        # Execute the strategy
        ...

    def estimate_roi(self) -> float:
        return 2.0  # Expected 2x return
```

Drop your strategy in `strategies/` and it will be auto-discovered.

## License

MIT
