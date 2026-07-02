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

    # agent limits
    max_agent_tokens: int = 4096
    # the evaluator panel writes a whole game's tags + syntheses in one response, so it needs a far
    # larger output budget than the per-move game agents and per-turn commentators
    max_evaluator_tokens: int = 32000
    agent_reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    max_invalid_retries: int = 3
    max_consecutive_provider_failures: int = 6
    # how many commentator-panel members may call their providers at once per commentary point
    max_concurrent_commentators: int = 4

    # game and tournament defaults
    max_turns: int = 150
    allow_private_messages: bool = False
    max_negotiation_rounds: int = 3
    elo_k_factor: float = 32.0
    output_directory: Path = Path("data")


def load_settings() -> Settings:
    """Load settings from the environment and ``.env``.

    Returns:
        The populated settings.
    """
    return Settings()
