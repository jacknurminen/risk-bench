"""Player model-spec resolution, shared by the CLI and the web API.

A spec is 'human' (web games), 'random' or 'provider:model' (e.g. 'anthropic:claude-opus-5-5'), optionally
suffixed with ':<effort>' (low / mid- via 'medium' / high) to set that player's reasoning effort, e.g.
'anthropic:claude-opus-5-5:low'. Without the suffix the global default effort applies. Any model id
the provider accepts may be named, not only the ones listed in ``agents.models.MODELS`` — the
registry is what the setup form offers and what usage reports can price, not a whitelist.
Providers: anthropic, openai, google.
"""

import random
from pathlib import Path
from typing import Final

import claude_agent_sdk as sdk

from risk_bench.agents.anthropic import AnthropicAgent
from risk_bench.agents.base import Agent, ReasoningEffort
from risk_bench.agents.google import GeminiAgent
from risk_bench.agents.models import Provider
from risk_bench.agents.openai import OpenAIAgent
from risk_bench.agents.tracing import TraceWriter, TracingAgent
from risk_bench.config import Settings
from risk_bench.game.types import PlayerId
from risk_bench.players.base import Player
from risk_bench.players.human import HumanPlayer
from risk_bench.players.llm import LLMPlayer
from risk_bench.players.random import RandomPlayer
from risk_bench.runner.commentary import Commentator
from risk_bench.runner.evaluation_agent import AgentEvaluator, JudgeLoop, ToolUseLoop
from risk_bench.runner.evaluation_agent_sdk import ClaudeAgentSdkLoop
from risk_bench.runner.evaluation_v4 import Evaluator
from risk_bench.tournament.runner import PlayerFactory

_PROVIDER_NAMES: Final[frozenset[str]] = frozenset(Provider)
# 'agent:<provider>:<model>[:<effort>]' selects the tool-using agent judge instead of the single-call judge
AGENT_JUDGE_PREFIX: Final[str] = "agent:"
_AGENT_SDK_EFFORTS: Final[dict[str, sdk.EffortLevel]] = {
    "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max",
}

type PanelEvaluator = Evaluator | AgentEvaluator


def resolve_model(spec: str) -> str:
    """Resolve a player spec to the concrete model id it names.

    Args:
        spec: A 'provider:model' spec ('random' is not a model).

    Returns:
        The concrete model id string.

    Raises:
        ValueError: If the spec is malformed or names an unknown provider.
    """
    model_spec, _ = _split_effort(spec=spec)
    provider, _, model = model_spec.partition(":")
    if not model:
        raise ValueError(f"'{spec}' is not 'random' or a provider:model spec.")
    if provider not in _PROVIDER_NAMES:
        raise ValueError(f"Unknown provider '{provider}'; use {', '.join(Provider)}.")
    return model


def named_specs(specs: list[str]) -> dict[str, str]:
    """Assign a unique player name to every spec, suffixing duplicates with #2, #3, ...

    Args:
        specs: The player specs, in the order they were given (seating is shuffled later).

    Returns:
        Player name (resolved model id or 'random') mapped to its original spec.

    Raises:
        ValueError: If any spec is invalid.
    """
    if specs.count("human") > 1:
        raise ValueError("Only one human player is supported per game.")
    named = {}
    for spec in specs:
        base_name = spec if spec in {"random", "human"} else resolve_model(spec=spec)
        name = base_name
        suffix = 2
        while name in named:
            name = f"{base_name}#{suffix}"
            suffix += 1
        named[name] = spec
    return named


def player_factory(
    spec: str, settings: Settings, trace: TraceWriter | None, human_mailbox: Path | None = None
) -> PlayerFactory:
    """Create a factory producing fresh players for the given spec.

    Args:
        spec: The player spec ('random' or a model spec).
        settings: Provides API keys and agent limits.
        trace: Records every provider API call when given; None disables tracing.
        human_mailbox: The web worker’s decision mailbox; required for a human seat.

    Returns:
        A factory taking (player_id, rng) and returning a new player.

    Raises:
        ValueError: If the spec is invalid or the provider's API key is missing.
    """
    if spec == "human":
        if human_mailbox is None:
            raise ValueError("Human players require a single journaled game; launch one from the web app.")
        return lambda player_id, rng: HumanPlayer(player_id=player_id, mailbox=human_mailbox)
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
        )

    return create_llm_player


def build_agent(spec: str, settings: Settings, max_tokens: int) -> Agent:
    """Build the provider client for a model spec.

    Args:
        spec: A 'provider:model' spec.
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
            raise ValueError(f"Unknown provider '{unknown_provider}'; use {', '.join(Provider)}.")


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
) -> tuple[PanelEvaluator, ...]:
    """Build the evaluator panel: one independent research coder per member spec.

    Mirrors ``build_commentators`` — members are built in panel order and each tags its trace
    records with its index. The caller decides between an offline sweep and live turn updates.
    A spec prefixed 'agent:' (e.g. 'agent:anthropic:claude-opus-5-5:high') builds the agent judge,
    which assesses whole games only; every other spec builds the single-call judge as before.

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


def reject_whole_game_only_evaluators(evaluator_specs: list[str]) -> None:
    """Refuse a round-by-round panel that contains an agent judge.

    Args:
        evaluator_specs: The panel's member specs.

    Raises:
        ValueError: If any member is an agent judge, which assesses whole games only.
    """
    agent_judges = [spec for spec in evaluator_specs if spec.startswith(AGENT_JUDGE_PREFIX)]
    if agent_judges:
        raise ValueError(f"Agent judges evaluate whole games only, not live rounds: {', '.join(agent_judges)}.")


def _build_evaluator(index: int, spec: str, settings: Settings, trace: TraceWriter | None) -> PanelEvaluator:
    if spec.startswith(AGENT_JUDGE_PREFIX):
        return _build_agent_judge(index=index, spec=spec, settings=settings, trace=trace)
    # a member emits every tag and synthesis for the whole game in one response,
    # so it gets the evaluator budget rather than the per-move agent budget
    agent = build_agent(spec=spec, settings=settings, max_tokens=settings.max_evaluator_tokens)
    if trace is not None:
        agent = TracingAgent(agent=agent, model=resolve_model(spec=spec), caller=f"evaluator[{index}]", writer=trace)
    return Evaluator(agent=agent, spec=spec)


def _build_agent_judge(index: int, spec: str, settings: Settings, trace: TraceWriter | None) -> AgentEvaluator:
    model_spec = spec.removeprefix(AGENT_JUDGE_PREFIX)
    caller = f"evaluator[{index}]"
    loop: JudgeLoop
    if model_spec.partition(":")[0] == "anthropic":
        # Claude runs through the Agent SDK, whose effort scale extends past the shared low/medium/high
        head, _, tail = model_spec.rpartition(":")
        has_effort = head.count(":") >= 1 and tail in _AGENT_SDK_EFFORTS
        loop = ClaudeAgentSdkLoop(
            model=resolve_model(spec=head if has_effort else model_spec),
            effort=_AGENT_SDK_EFFORTS[tail] if has_effort else None,
            api_key=_require_api_key(provider="anthropic", settings=settings),
            max_turns=settings.max_agent_judge_turns,
            max_budget_usd=settings.max_agent_judge_budget_usd,
            trace=trace,
            caller=caller,
        )
    else:
        # other providers drive the same tools through the provider-neutral respond loop
        agent = build_agent(spec=model_spec, settings=settings, max_tokens=settings.max_evaluator_tokens)
        model = resolve_model(spec=model_spec)
        if trace is not None:
            agent = TracingAgent(agent=agent, model=model, caller=caller, writer=trace)
        loop = ToolUseLoop(
            agent=agent,
            model=model,
            max_turns=settings.max_agent_judge_turns,
            max_budget_usd=settings.max_agent_judge_budget_usd,
        )
    return AgentEvaluator(loop=loop, spec=spec)


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
        "anthropic": settings.anthropic_aalto_api_key,
        "openai": settings.openai_api_key,
        "google": settings.google_api_key,
    }
    api_key = keys_by_provider.get(provider)
    if not api_key:
        key_name = "ANTHROPIC_AALTO_API_KEY" if provider == "anthropic" else f"{provider.upper()}_API_KEY"
        raise ValueError(f"No API key configured for '{provider}'; set {key_name} in the environment or .env.")
    return api_key
