"""The models the benchmark can seat, per provider, with the list prices used to cost a run.

``MODELS`` is the single source of truth: adding an entry there is all it takes for a model to be
selectable from the CLI, the setup form and the evaluation panel, and to be priced in every usage
report. Prices are USD per million tokens, checked 2026-09-08 against the providers' pricing pages:
https://platform.claude.com/docs/en/about-claude/pricing
https://ai.google.dev/gemini-api/docs/pricing
https://developers.openai.com/api/docs/pricing
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENAI = "openai"


@dataclass(frozen=True)
class ModelPricing:
    """List prices for one model in USD per million tokens.

    The cache rates price the cached share of input tokens: a cache write is billed against the
    input rate (at a premium on Anthropic, at par elsewhere) and a cache read at a discount.
    """

    input_usd_per_million: float
    cache_write_usd_per_million: float
    cache_read_usd_per_million: float
    output_usd_per_million: float


@dataclass(frozen=True)
class BenchmarkModel:
    """A model the benchmark can seat: who serves it, and what a call to it costs."""

    id: str
    provider: Provider
    pricing: ModelPricing


# Cache rates are formulas, not per-model figures: every current provider discounts a cached read
# to a tenth of its input rate, and only Anthropic charges a premium to write a 5-minute cache
# entry. Deriving them keeps a new model's entry down to the two prices its pricing page lists.
_CACHE_WRITE_MULTIPLIER: Final[dict[Provider, float]] = {
    Provider.ANTHROPIC: 1.25,
    Provider.GOOGLE: 1.00,
    Provider.OPENAI: 1.00,
}
_CACHE_READ_MULTIPLIER: Final[float] = 0.10


def _model(model_id: str, provider: Provider, input_usd: float, output_usd: float) -> BenchmarkModel:
    return BenchmarkModel(
        id=model_id,
        provider=provider,
        pricing=ModelPricing(
            input_usd_per_million=input_usd,
            cache_write_usd_per_million=input_usd * _CACHE_WRITE_MULTIPLIER[provider],
            cache_read_usd_per_million=input_usd * _CACHE_READ_MULTIPLIER,
            output_usd_per_million=output_usd,
        ),
    )


# Ordered most to least capable within each provider; the setup form offers a provider's first
# entry by default, so the flagship leads.
MODELS: Final[tuple[BenchmarkModel, ...]] = (
    # anthropic
    _model(model_id="claude-fable-5", provider=Provider.ANTHROPIC, input_usd=10.00, output_usd=50.00),
    _model(model_id="claude-opus-5", provider=Provider.ANTHROPIC, input_usd=5.00, output_usd=25.00),
    _model(model_id="claude-sonnet-5", provider=Provider.ANTHROPIC, input_usd=2.00, output_usd=10.00),
    _model(model_id="claude-haiku-4-5", provider=Provider.ANTHROPIC, input_usd=1.00, output_usd=5.00),
    # google; the pro rate is for prompts up to 200k tokens ($4.00 / $18.00 above that), and the
    # 3.8 flash rate is introductory through 2026-12-31, doubling to $1.50 / $7.50 on 2027-01-01
    _model(model_id="gemini-3.1-pro-preview", provider=Provider.GOOGLE, input_usd=2.00, output_usd=12.00),
    _model(model_id="gemini-3.8-flash", provider=Provider.GOOGLE, input_usd=0.75, output_usd=3.75),
    _model(model_id="gemini-3.5-flash-lite", provider=Provider.GOOGLE, input_usd=0.30, output_usd=2.50),
    # openai
    _model(model_id="gpt-6-astra", provider=Provider.OPENAI, input_usd=10.00, output_usd=50.00),
    _model(model_id="gpt-5.6-sol", provider=Provider.OPENAI, input_usd=4.00, output_usd=20.00),
    _model(model_id="gpt-5.6-terra", provider=Provider.OPENAI, input_usd=2.00, output_usd=12.00),
    _model(model_id="gpt-5.6-luna", provider=Provider.OPENAI, input_usd=0.20, output_usd=1.20),
)

_PRICING_BY_MODEL_ID: Final[dict[str, ModelPricing]] = {model.id: model.pricing for model in MODELS}


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
    pricing = _PRICING_BY_MODEL_ID.get(model)
    if pricing is None:
        return None
    uncached_input_tokens = max(0, input_tokens - cache_write_tokens - cache_read_tokens)
    return (
        uncached_input_tokens * pricing.input_usd_per_million
        + cache_write_tokens * pricing.cache_write_usd_per_million
        + cache_read_tokens * pricing.cache_read_usd_per_million
        + output_tokens * pricing.output_usd_per_million
    ) / 1_000_000
