"""The models the benchmark can seat, per provider, with the list prices used to cost a run.

``MODELS`` is the single source of truth: adding an entry there is all it takes for a model to be
selectable from the CLI, the setup form and the evaluation panel, and to be priced in every usage
report. Prices are USD per million tokens, checked 2026-09-08; Opus 5.5 and GPT-6 Sol/Luna
updated 2026-09-23, Sonnet 5.5 updated 2026-09-29; Haiku 5.5, Fable 5.1,
GPT-6.1 Sol and restored previous versions checked 2026-10-08 against the providers' pricing pages:
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


# Default cache rates, with model-specific overrides for newer pricing schedules.
_CACHE_WRITE_MULTIPLIER: Final[dict[Provider, float]] = {
    Provider.ANTHROPIC: 1.25,
    Provider.GOOGLE: 1.00,
    Provider.OPENAI: 1.00,
}
_CACHE_READ_MULTIPLIER: Final[float] = 0.10


def _model(
    model_id: str,
    provider: Provider,
    input_usd: float,
    output_usd: float,
    *,
    cache_read_multiplier: float = _CACHE_READ_MULTIPLIER,
    cache_write_multiplier: float | None = None,
) -> BenchmarkModel:
    return BenchmarkModel(
        id=model_id,
        provider=provider,
        pricing=ModelPricing(
            input_usd_per_million=input_usd,
            cache_write_usd_per_million=input_usd
            * (_CACHE_WRITE_MULTIPLIER[provider] if cache_write_multiplier is None else cache_write_multiplier),
            cache_read_usd_per_million=input_usd * cache_read_multiplier,
            output_usd_per_million=output_usd,
        ),
    )


# Current models first, then previous versions within each provider. The setup form offers
# a provider's first entry by default, so the flagship leads.
MODELS: Final[tuple[BenchmarkModel, ...]] = (
    # anthropic
    _model(
        model_id="claude-fable-5-1", provider=Provider.ANTHROPIC,
        input_usd=10.00, output_usd=50.00, cache_read_multiplier=0.025,
    ),
    _model(
        model_id="claude-opus-5-5",
        provider=Provider.ANTHROPIC,
        input_usd=4.00,
        output_usd=20.00,
        cache_read_multiplier=0.05,
    ),
    _model(model_id="claude-sonnet-5-5", provider=Provider.ANTHROPIC, input_usd=2.00, output_usd=10.00),
    # Haiku 5.5 base prices apply through 100k prompt tokens; longer calls cost 5x.
    _model(model_id="claude-haiku-5-5", provider=Provider.ANTHROPIC, input_usd=0.10, output_usd=0.50),
    _model(model_id="claude-fable-5", provider=Provider.ANTHROPIC, input_usd=10.00, output_usd=50.00),
    _model(model_id="claude-opus-5", provider=Provider.ANTHROPIC, input_usd=5.00, output_usd=25.00),
    _model(model_id="claude-sonnet-5", provider=Provider.ANTHROPIC, input_usd=2.00, output_usd=10.00),
    _model(model_id="claude-haiku-4-5", provider=Provider.ANTHROPIC, input_usd=1.00, output_usd=5.00),
    # google; the pro rate is for prompts up to 200k tokens ($4.00 / $18.00 above that), and the
    # 3.8 flash rate is introductory through 2026-12-31, doubling to $1.50 / $7.50 on 2027-01-01
    _model(model_id="gemini-3.1-pro-preview", provider=Provider.GOOGLE, input_usd=2.00, output_usd=12.00),
    _model(model_id="gemini-3.8-flash", provider=Provider.GOOGLE, input_usd=0.75, output_usd=3.75),
    _model(model_id="gemini-3.5-flash-lite", provider=Provider.GOOGLE, input_usd=0.30, output_usd=2.50),
    _model(model_id="gemini-3.7-flash", provider=Provider.GOOGLE, input_usd=0.75, output_usd=3.75),
    _model(model_id="gemini-3.1-flash-lite", provider=Provider.GOOGLE, input_usd=0.25, output_usd=1.50),
    # Gemini 3 Pro Preview was shut down; don't offer it as a playable predecessor.
    # openai
    _model(model_id="gpt-6-astra", provider=Provider.OPENAI, input_usd=10.00, output_usd=50.00),
    _model(
        model_id="gpt-6.1-sol", provider=Provider.OPENAI, input_usd=2.00, output_usd=10.00,
        cache_read_multiplier=0.05, cache_write_multiplier=1.25,
    ),
    _model(
        model_id="gpt-6-sol", provider=Provider.OPENAI, input_usd=2.00, output_usd=10.00, cache_write_multiplier=1.25
    ),
    _model(model_id="gpt-5.6-terra", provider=Provider.OPENAI, input_usd=2.00, output_usd=12.00),
    _model(
        model_id="gpt-6-luna", provider=Provider.OPENAI, input_usd=0.10, output_usd=0.50, cache_write_multiplier=1.25
    ),
    _model(model_id="gpt-5.6-sol", provider=Provider.OPENAI, input_usd=4.00, output_usd=20.00),
    _model(model_id="gpt-5.6-luna", provider=Provider.OPENAI, input_usd=0.20, output_usd=1.20),
)
_PRICING_BY_MODEL_ID: Final[dict[str, ModelPricing]] = {
    model.id: model.pricing for model in MODELS
}


def estimate_cost_usd(
    model: str, input_tokens: int, cache_write_tokens: int, cache_read_tokens: int, output_tokens: int
) -> float | None:
    """Estimate one API call's list-price cost, including caching and supported prompt-length tiers.

    Args:
        model: The concrete model id the call was made against.
        input_tokens: Total input tokens for this call, including any cached share.
        cache_write_tokens: The share of input tokens written to the prompt cache.
        cache_read_tokens: The share of input tokens served from the prompt cache.
        output_tokens: Total output (including reasoning) tokens for this call.

    Returns:
        The estimated cost in USD, or None when the model's pricing is unknown.
    """
    pricing = _PRICING_BY_MODEL_ID.get(model)
    if pricing is None:
        return None
    uncached_input_tokens = max(0, input_tokens - cache_write_tokens - cache_read_tokens)
    input_multiplier = output_multiplier = 1.0
    if model == "claude-haiku-5-5" and input_tokens > 100_000:
        input_multiplier = output_multiplier = 5.0
    elif model == "gpt-6.1-sol" and input_tokens > 272_000:
        input_multiplier, output_multiplier = 2.0, 1.5
    input_cost = (
        uncached_input_tokens * pricing.input_usd_per_million
        + cache_write_tokens * pricing.cache_write_usd_per_million
        + cache_read_tokens * pricing.cache_read_usd_per_million
    )
    output_cost = output_tokens * pricing.output_usd_per_million
    return (input_cost * input_multiplier + output_cost * output_multiplier) / 1_000_000
