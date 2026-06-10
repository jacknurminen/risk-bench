from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    max_invalid_retries: int = 3

    # game and tournament defaults
    max_turns: int = 150
    allow_private_messages: bool = False
    elo_k_factor: float = 32.0
    output_directory: Path = Path("data")


def load_settings() -> Settings:
    """Load settings from the environment and ``.env``.

    Returns:
        The populated settings.
    """
    return Settings()
