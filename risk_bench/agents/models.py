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
    """List prices for one model in USD per million tokens.

    The cache rates price the cached share of input tokens: Anthropic bills 5-minute cache
    writes at 1.25x the input rate and cache reads at 0.1x; OpenAI and Google charge no write
    premium (their write rate equals the input rate) and discount cached reads to ~10% and ~25%
    of the input rate respectively.
    """

    input_usd_per_million: float
    cache_write_usd_per_million: float
    cache_read_usd_per_million: float
    output_usd_per_million: float


def _pricing(input_usd: float, cache_write_usd: float, cache_read_usd: float, output_usd: float) -> ModelPricing:
    return ModelPricing(
        input_usd_per_million=input_usd,
        cache_write_usd_per_million=cache_write_usd,
        cache_read_usd_per_million=cache_read_usd,
        output_usd_per_million=output_usd,
    )


MODEL_PRICING: Final[dict[str, ModelPricing]] = {
    # anthropic (cache write 1.25x input, cache read 0.1x)
    AnthropicModel.LO: _pricing(input_usd=1.00, cache_write_usd=1.25, cache_read_usd=0.10, output_usd=5.00),
    AnthropicModel.MID: _pricing(input_usd=3.00, cache_write_usd=3.75, cache_read_usd=0.30, output_usd=15.00),
    AnthropicModel.HI: _pricing(input_usd=5.00, cache_write_usd=6.25, cache_read_usd=0.50, output_usd=25.00),
    # google ('hi' uses the <=200k-token prompt rate; $4.00 / $18.00 above that); implicit
    # caching has no write premium and bills cached reads at 25% of the input rate
    GoogleModel.LO: _pricing(input_usd=0.25, cache_write_usd=0.25, cache_read_usd=0.0625, output_usd=1.50),
    GoogleModel.MID: _pricing(input_usd=1.50, cache_write_usd=1.50, cache_read_usd=0.375, output_usd=9.00),
    GoogleModel.HI: _pricing(input_usd=2.00, cache_write_usd=2.00, cache_read_usd=0.50, output_usd=12.00),
    # openai (no write premium; cached input at 10% of the input rate)
    OpenAIModel.LO: _pricing(input_usd=0.75, cache_write_usd=0.75, cache_read_usd=0.075, output_usd=4.50),
    OpenAIModel.MID: _pricing(input_usd=2.50, cache_write_usd=2.50, cache_read_usd=0.25, output_usd=15.00),
    OpenAIModel.HI: _pricing(input_usd=5.00, cache_write_usd=5.00, cache_read_usd=0.50, output_usd=30.00),
}


def estimate_cost_usd(
    model: str, input_tokens: int, cache_write_tokens: int, cache_read_tokens: int, output_tokens: int
) -> float | None:
    """Estimate the list-price cost of one or more API calls to a model, cache-aware.

    Args:
        model: The concrete model id the calls were made against.
        input_tokens: Total input tokens across the calls, including any cached share.
        cache_write_tokens: The share of input tokens written to the prompt cache.
        cache_read_tokens: The share of input tokens served from the prompt cache.
        output_tokens: Total output (including reasoning) tokens across the calls.

    Returns:
        The estimated cost in USD, or None when the model's pricing is unknown.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return None
    uncached_input_tokens = max(0, input_tokens - cache_write_tokens - cache_read_tokens)
    return (
        uncached_input_tokens * pricing.input_usd_per_million
        + cache_write_tokens * pricing.cache_write_usd_per_million
        + cache_read_tokens * pricing.cache_read_usd_per_million
        + output_tokens * pricing.output_usd_per_million
    ) / 1_000_000
