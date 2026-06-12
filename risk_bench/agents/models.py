"""Benchmark model tiers per provider: lo / mid / hi mapped to concrete model identifiers.

Tiers pick comparable price-capability points across providers so cross-provider
matchups are meaningful. Prices are input/output USD per million tokens,
checked 2026-06-10 against the providers' pricing pages:
https://platform.claude.com/docs/en/about-claude/pricing
https://ai.google.dev/gemini-api/docs/pricing
https://developers.openai.com/api/docs/pricing
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class AnthropicModel(StrEnum):
    LO = "claude-haiku-4-5"
    MID = "claude-sonnet-4-6"
    HI = "claude-opus-4-8"


class GoogleModel(StrEnum):
    LO = "gemini-3.1-flash-lite"
    MID = "gemini-3.5-flash"
    HI = "gemini-3.1-pro-preview"


class OpenAIModel(StrEnum):
    LO = "gpt-5.4-mini"
    MID = "gpt-5.4"
    HI = "gpt-5.5"


@dataclass(frozen=True)
class ModelPricing:
    """List prices for one model in USD per million tokens."""

    input_usd_per_million: float
    output_usd_per_million: float


MODEL_PRICING: Final[dict[str, ModelPricing]] = {
    # anthropic
    AnthropicModel.LO: ModelPricing(input_usd_per_million=1.00, output_usd_per_million=5.00),
    AnthropicModel.MID: ModelPricing(input_usd_per_million=3.00, output_usd_per_million=15.00),
    AnthropicModel.HI: ModelPricing(input_usd_per_million=5.00, output_usd_per_million=25.00),
    # google ('hi' uses the <=200k-token prompt rate; $4.00 / $18.00 above that)
    GoogleModel.LO: ModelPricing(input_usd_per_million=0.25, output_usd_per_million=1.50),
    GoogleModel.MID: ModelPricing(input_usd_per_million=1.50, output_usd_per_million=9.00),
    GoogleModel.HI: ModelPricing(input_usd_per_million=2.00, output_usd_per_million=12.00),
    # openai
    OpenAIModel.LO: ModelPricing(input_usd_per_million=0.75, output_usd_per_million=4.50),
    OpenAIModel.MID: ModelPricing(input_usd_per_million=2.50, output_usd_per_million=15.00),
    OpenAIModel.HI: ModelPricing(input_usd_per_million=5.00, output_usd_per_million=30.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimate the list-price cost of one or more API calls to a model.

    Args:
        model: The concrete model id the calls were made against.
        input_tokens: Total input tokens across the calls.
        output_tokens: Total output (including reasoning) tokens across the calls.

    Returns:
        The estimated cost in USD, or None when the model's pricing is unknown.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return None
    return (input_tokens * pricing.input_usd_per_million + output_tokens * pricing.output_usd_per_million) / 1_000_000
