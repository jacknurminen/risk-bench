from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration sourced from the environment (or a local .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # API keys
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    xai_api_key: str | None = None
    google_api_key: str | None = None

    # Game settings
    max_turns: int = 500
    max_agent_retries: int = 3
    reaction_window_cap: int = 12

    # Agent inference
    max_agent_tokens: int = 512

    # Web server
    default_host: str = "0.0.0.0"
    default_port: int = 8000
