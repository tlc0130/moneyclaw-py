"""Pydantic Settings — type-safe configuration loaded from env/.env file."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    token: str = ""
    chat_id: str = ""


class LLMSettings(BaseSettings):
    """LLM provider configuration."""

    # Layer 1: local
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    # Layer 2: cheap
    deepseek_api_key: str = ""
    groq_api_key: str = ""

    # Layer 3: premium
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Budget
    daily_llm_budget: float = 1.0  # USD


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK_")

    max_trade_amount: float = 50.0  # Max single trade in USD
    max_daily_loss: float = 100.0  # Stop-loss per day
    approval_threshold: float = 50.0  # Require approval above this amount
    cooldown_after_losses: int = 3  # Pause after N consecutive losses
    per_strategy_daily_loss: float = 30.0  # Per-strategy daily loss cap (0 = disabled)
    max_position_ratio: float = 0.5  # Max ratio of portfolio in single asset (0-1)
    dry_run: bool = True  # Default: simulate all trades


class ExchangeConfig(BaseSettings):
    """Config for a single exchange connection."""

    api_key: str = ""
    secret: str = ""
    password: str = ""  # Some exchanges need a passphrase


class ExchangeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXCHANGE_")

    # Default exchange for strategies that don't specify one
    default_exchange: str = "binance"
    dry_run: bool = True  # Global dry_run override
    # Per-exchange configs loaded from env: BINANCE_API_KEY, etc.
    binance_api_key: str = ""
    binance_secret: str = ""
    okx_api_key: str = ""
    okx_secret: str = ""
    okx_password: str = ""


class DataSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATA_")

    duckdb_path: str = "data/market.duckdb"
    price_poll_interval: int = 300  # 5 minutes
    news_poll_interval: int = 900  # 15 minutes


class Settings(BaseSettings):
    """Root settings — loads from .env file and environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Sub-configs
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    exchange: ExchangeSettings = Field(default_factory=ExchangeSettings)
    data: DataSettings = Field(default_factory=DataSettings)

    # General
    db_path: str = "data/moneyclaw.db"
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    log_level: str = "INFO"

    # Agent behavior
    scan_interval: int = 60  # Seconds between scans
    strategies_dir: str = "strategies"
