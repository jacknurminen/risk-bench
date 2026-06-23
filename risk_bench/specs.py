"""Player model-spec resolution, shared by the CLI and the web API.

A spec is 'random', 'provider:tier' (e.g. 'anthropic:hi'), or 'provider:model'
with an explicit model id. Providers: anthropic, openai, google.
"""

import random
from typing import Final

from risk_bench.agents.anthropic import AnthropicAgent
from risk_bench.agents.base import Agent
from risk_bench.agents.google import GeminiAgent
from risk_bench.agents.models import AnthropicModel, GoogleModel, OpenAIModel
from risk_bench.agents.openai import OpenAIAgent
from risk_bench.agents.tracing import TraceWriter, TracingAgent
from risk_bench.config import Settings
from risk_bench.game.types import PlayerId
from risk_bench.players.base import Player
from risk_bench.players.llm import LLMPlayer
from risk_bench.players.random import RandomPlayer
from risk_bench.runner.commentary import RUBRICS, Commentator
from risk_bench.tournament.runner import PlayerFactory

TIER_MODELS_BY_PROVIDER: Final[dict[str, type[AnthropicModel] | type[GoogleModel] | type[OpenAIModel]]] = {
    "anthropic": AnthropicModel,
    "google": GoogleModel,
    "openai": OpenAIModel,
}


def resolve_model(spec: str) -> str:
    """Resolve a player spec to a concrete model id.

    Args:
        spec: A 'provider:tier' or 'provider:model' spec ('random' is not a model).

    Returns:
        The concrete model id string.

    Raises:
        ValueError: If the spec is malformed or names an unknown provider.
    """
    provider, _, model_or_tier = spec.partition(":")
    if not model_or_tier:
        raise ValueError(f"'{spec}' is not 'random' or a provider:tier / provider:model spec.")
    tier_models = TIER_MODELS_BY_PROVIDER.get(provider)
    if tier_models is None:
        raise ValueError(f"Unknown provider '{provider}'; use anthropic, openai, or google.")
    if model_or_tier.upper() in tier_models.__members__:
        return tier_models[model_or_tier.upper()].value
    return model_or_tier


def named_specs(specs: list[str]) -> dict[str, str]:
    """Assign a unique player name to every spec, suffixing duplicates with #2, #3, ...

    Args:
        specs: The player specs in seating order.

    Returns:
        Player name (resolved model id or 'random') mapped to its original spec.

    Raises:
        ValueError: If any spec is invalid.
    """
    named = {}
    for spec in specs:
        base_name = "random" if spec == "random" else resolve_model(spec=spec)
        name = base_name
        suffix = 2
        while name in named:
            name = f"{base_name}#{suffix}"
            suffix += 1
        named[name] = spec
    return named


def player_factory(spec: str, settings: Settings, trace: TraceWriter | None) -> PlayerFactory:
    """Create a factory producing fresh players for the given spec.

    Args:
        spec: The player spec ('random' or a model spec).
        settings: Provides API keys and agent limits.
        trace: Records every provider API call when given; None disables tracing.

    Returns:
        A factory taking (player_id, rng) and returning a new player.

    Raises:
        ValueError: If the spec is invalid or the provider's API key is missing.
    """
    if spec == "random":
        return lambda player_id, rng: RandomPlayer(player_id=player_id, rng=rng)
    agent = build_agent(spec=spec, settings=settings)
    model = resolve_model(spec=spec)

    def create_llm_player(player_id: PlayerId, rng: random.Random) -> Player:
        player_agent = (
            agent if trace is None else TracingAgent(agent=agent, model=model, caller=player_id, writer=trace)
        )
        return LLMPlayer(
            player_id=player_id,
            agent=player_agent,
            max_invalid_retries=settings.max_invalid_retries,
            max_consecutive_provider_failures=settings.max_consecutive_provider_failures,
            rng=rng,
        )

    return create_llm_player


def build_agent(spec: str, settings: Settings) -> Agent:
    """Build the provider client for a model spec.

    Args:
        spec: A 'provider:tier' or 'provider:model' spec.
        settings: Provides API keys and the token limit.

    Returns:
        The provider agent.

    Raises:
        ValueError: If the provider is unknown or its API key is not configured.
    """
    provider = spec.partition(":")[0]
    model = resolve_model(spec=spec)
    api_key = _require_api_key(provider=provider, settings=settings)
    match provider:
        case "anthropic":
            return AnthropicAgent(
                model=model,
                api_key=api_key,
                max_tokens=settings.max_agent_tokens,
                reasoning_effort=settings.agent_reasoning_effort,
            )
        case "openai":
            return OpenAIAgent(
                model=model,
                api_key=api_key,
                max_tokens=settings.max_agent_tokens,
                reasoning_effort=settings.agent_reasoning_effort,
            )
        case "google":
            return GeminiAgent(
                model=model,
                api_key=api_key,
                max_tokens=settings.max_agent_tokens,
                reasoning_effort=settings.agent_reasoning_effort,
            )
        case unknown_provider:
            raise ValueError(f"Unknown provider '{unknown_provider}'; use anthropic, openai, or google.")


def build_commentators(
    rubric_specs: dict[str, str], settings: Settings, trace: TraceWriter | None
) -> tuple[Commentator, ...]:
    """Build one commentator per rubric from a rubric→model-spec mapping.

    Args:
        rubric_specs: Maps each rubric name (e.g. 'skill', 'conduct') to the model spec that
            should run it; empty for an uncommentated game.
        settings: Provides API keys and the token limit.
        trace: Records the commentators' API calls when given; None disables tracing.

    Returns:
        One commentator per requested rubric, in the canonical rubric order.

    Raises:
        ValueError: If a rubric name is unknown, or a spec is invalid or its API key is missing.
    """
    unknown = set(rubric_specs) - set(RUBRICS)
    if unknown:
        raise ValueError(f"Unknown commentator rubric(s) {sorted(unknown)}; choose from {sorted(RUBRICS)}.")
    return tuple(
        _build_commentator(rubric_name=name, spec=rubric_specs[name], settings=settings, trace=trace)
        for name in RUBRICS
        if name in rubric_specs
    )


def _build_commentator(rubric_name: str, spec: str, settings: Settings, trace: TraceWriter | None) -> Commentator:
    agent = build_agent(spec=spec, settings=settings)
    if trace is not None:
        agent = TracingAgent(
            agent=agent, model=resolve_model(spec=spec), caller=f"commentator:{rubric_name}", writer=trace
        )
    return Commentator(agent=agent, rubric=RUBRICS[rubric_name])


def parse_commentator_specs(values: list[str]) -> dict[str, str]:
    """Parse CLI '--commentator' values into a rubric→model-spec mapping.

    Each value is either 'rubric=spec' (assigns one rubric) or a bare 'spec' (assigns every
    rubric to that one model). Later values override earlier ones for the same rubric.

    Args:
        values: Raw '--commentator' option values, possibly empty.

    Returns:
        A rubric→spec mapping; empty when no values were given.

    Raises:
        ValueError: If a value names an unknown rubric or is malformed.
    """
    rubric_specs: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            rubric_specs = {name: value for name in RUBRICS}
            continue
        rubric_name, _, spec = value.partition("=")
        rubric_name, spec = rubric_name.strip(), spec.strip()
        if rubric_name not in RUBRICS:
            raise ValueError(f"Unknown commentator rubric '{rubric_name}'; choose from {sorted(RUBRICS)}.")
        if not spec:
            raise ValueError(f"Commentator rubric '{rubric_name}' has no model spec; use '{rubric_name}=<spec>'.")
        rubric_specs[rubric_name] = spec
    return rubric_specs


def _require_api_key(provider: str, settings: Settings) -> str:
    keys_by_provider = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "google": settings.google_api_key,
    }
    api_key = keys_by_provider.get(provider)
    if api_key is None:
        raise ValueError(f"No API key configured for '{provider}'; set {provider.upper()}_API_KEY.")
    return api_key
