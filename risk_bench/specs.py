"""Player model-spec resolution, shared by the CLI and the web API.

A spec is 'random', 'provider:tier' (e.g. 'anthropic:hi'), or 'provider:model'
with an explicit model id, each optionally suffixed with ':<effort>' (low / mid-
via 'medium' / high) to set that player's reasoning effort, e.g.
'anthropic:hi:low'. Without the suffix the global default effort applies.
Providers: anthropic, openai, google.
"""

import random
from typing import Final

from risk_bench.agents.anthropic import AnthropicAgent
from risk_bench.agents.base import Agent, ReasoningEffort
from risk_bench.agents.google import GeminiAgent
from risk_bench.agents.models import AnthropicModel, GoogleModel, OpenAIModel
from risk_bench.agents.openai import OpenAIAgent
from risk_bench.agents.tracing import TraceWriter, TracingAgent
from risk_bench.config import Settings
from risk_bench.game.types import PlayerId
from risk_bench.players.base import Player
from risk_bench.players.llm import LLMPlayer
from risk_bench.players.random import RandomPlayer
from risk_bench.runner.commentary import Commentator
from risk_bench.runner.evaluation import Evaluator
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
    model_spec, _ = _split_effort(spec=spec)
    provider, _, model_or_tier = model_spec.partition(":")
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
        specs: The player specs, in the order they were given (seating is shuffled later).

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
    agent = build_agent(spec=spec, settings=settings, max_tokens=settings.max_agent_tokens)
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


def build_agent(spec: str, settings: Settings, max_tokens: int) -> Agent:
    """Build the provider client for a model spec.

    Args:
        spec: A 'provider:tier' or 'provider:model' spec.
        settings: Provides API keys and the default reasoning effort.
        max_tokens: Output token budget per response; differs by role (game agents
            answer one move at a time, the evaluator writes a whole game's worth).

    Returns:
        The provider agent.

    Raises:
        ValueError: If the provider is unknown or its API key is not configured.
    """
    model_spec, effort = _split_effort(spec=spec)
    provider = model_spec.partition(":")[0]
    model = resolve_model(spec=model_spec)
    reasoning_effort = effort if effort is not None else settings.agent_reasoning_effort
    api_key = _require_api_key(provider=provider, settings=settings)
    match provider:
        case "anthropic":
            return AnthropicAgent(
                model=model, api_key=api_key, max_tokens=max_tokens, reasoning_effort=reasoning_effort
            )
        case "openai":
            return OpenAIAgent(model=model, api_key=api_key, max_tokens=max_tokens, reasoning_effort=reasoning_effort)
        case "google":
            return GeminiAgent(model=model, api_key=api_key, max_tokens=max_tokens, reasoning_effort=reasoning_effort)
        case unknown_provider:
            raise ValueError(f"Unknown provider '{unknown_provider}'; use anthropic, openai, or google.")


def build_commentators(
    commentator_specs: list[str], settings: Settings, trace: TraceWriter | None
) -> tuple[Commentator, ...]:
    """Build the commentator panel: one independent annotator per member spec.

    Every member runs the same single full-vocabulary pass, so members are directly comparable
    for the inter-annotator agreement signal. They are built in panel order and each is tagged
    with its index, so its trace and journal records stay distinguishable from the others'.

    Args:
        commentator_specs: One model spec per panel member, in panel order; empty for no commentary.
        settings: Provides API keys and the token limit.
        trace: Records the panel's API calls when given; None disables tracing.

    Returns:
        One commentator per member spec, in panel order.

    Raises:
        ValueError: If a spec is invalid or its API key is missing.
    """
    return tuple(
        _build_commentator(index=index, spec=spec, settings=settings, trace=trace)
        for index, spec in enumerate(commentator_specs)
    )


def _build_commentator(index: int, spec: str, settings: Settings, trace: TraceWriter | None) -> Commentator:
    agent = build_agent(spec=spec, settings=settings, max_tokens=settings.max_agent_tokens)
    if trace is not None:
        agent = TracingAgent(agent=agent, model=resolve_model(spec=spec), caller=f"commentator[{index}]", writer=trace)
    return Commentator(agent=agent, spec=spec)


def build_evaluators(
    evaluator_specs: list[str], settings: Settings, trace: TraceWriter | None
) -> tuple[Evaluator, ...]:
    """Build the evaluator panel: one independent whole-game annotator per member spec.

    Mirrors ``build_commentators`` — members are built in panel order and each tags its trace
    records with its index — but every member reads the full game offline rather than per turn.

    Args:
        evaluator_specs: One model spec per panel member, in panel order; empty for no evaluation.
        settings: Provides API keys and the token limit.
        trace: Records the panel's API calls when given; None disables tracing.

    Returns:
        One evaluator per member spec, in panel order.

    Raises:
        ValueError: If a spec is invalid or its API key is missing.
    """
    return tuple(
        _build_evaluator(index=index, spec=spec, settings=settings, trace=trace)
        for index, spec in enumerate(evaluator_specs)
    )


def _build_evaluator(index: int, spec: str, settings: Settings, trace: TraceWriter | None) -> Evaluator:
    # a member emits every tag, arc, and synthesis for the whole game in one response,
    # so it gets the evaluator budget rather than the per-move agent budget
    agent = build_agent(spec=spec, settings=settings, max_tokens=settings.max_evaluator_tokens)
    if trace is not None:
        agent = TracingAgent(agent=agent, model=resolve_model(spec=spec), caller=f"evaluator[{index}]", writer=trace)
    return Evaluator(agent=agent, spec=spec)


def _split_effort(spec: str) -> tuple[str, ReasoningEffort | None]:
    """Split an optional trailing ':<effort>' off a spec, returning (model_spec, effort).

    A segment is treated as effort only when it names a ReasoningEffort
    (low/medium/high); otherwise the spec is returned untouched, so plain
    'provider:tier', 'provider:model', and 'random' specs are unaffected (tiers
    are lo/mid/hi, never the effort words, so there is no ambiguity).
    """
    head, _, tail = spec.rpartition(":")
    if head and tail.upper() in ReasoningEffort.__members__:
        return head, ReasoningEffort[tail.upper()]
    return spec, None


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
