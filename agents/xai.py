"""Grok adapter — uses OpenAI-compatible API via xAI endpoint."""

from __future__ import annotations

from agents.openai import OpenAIAgent


class GrokAgent(OpenAIAgent):
    """Grok via xAI's OpenAI-compatible endpoint."""

    def __init__(
        self,
        color: str,
        name: str,
        model: str = "grok-2",
        api_key: str | None = None,
        max_tokens: int = 512,
    ) -> None:
        super().__init__(
            color=color,
            name=name,
            model=model,
            api_key=api_key,
            base_url="https://api.x.ai/v1",
            max_tokens=max_tokens,
        )
