from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from risk_bench.agents.base import ReasoningEffort


class Settings(BaseSettings):
    """Application settings, loaded from environment variables and ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # provider API keys
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = None
    xai_api_key: str | None = None

    # agent limits; the budget covers hidden reasoning tokens too, so it must leave room for
    # a reasoning model to think AND still emit its tool call (4096 proved too tight for GPT)
    max_agent_tokens: int = 8192
    # Whole-game adjudication emits linked research units, requiring more output than gameplay calls.
    max_evaluator_tokens: int = 32000
    agent_reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    max_invalid_retries: int = 3
    # how many commentator-panel members may call their providers at once per commentary point
    max_concurrent_commentators: int = 4

    # game and tournament defaults
    max_turns: int = 150
    allow_private_messages: bool = False
    max_negotiation_rounds: int = 3
    elo_k_factor: float = 32.0
    output_directory: Path = Path("data/v3")


def load_settings() -> Settings:
    """Load settings from the environment and ``.env``.

    Returns:
        The populated settings.
    """
    return Settings()
