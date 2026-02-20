from __future__ import annotations

import os

# API keys
ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
XAI_API_KEY: str | None = os.getenv("XAI_API_KEY")
GOOGLE_API_KEY: str | None = os.getenv("GOOGLE_API_KEY")

# Default models
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "xai": "grok-2",
    "google": "gemini-1.5-flash",
    "random": "random",
}

# Game settings
MAX_TURNS = 500
MAX_AGENT_RETRIES = 3
REACTION_WINDOW_CAP = 12

# Agent inference
MAX_AGENT_TOKENS: int = 512

# Web server
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
