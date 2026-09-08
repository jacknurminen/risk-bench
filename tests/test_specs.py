import pytest

from risk_bench import specs
from risk_bench.agents.anthropic import AnthropicAgent
from risk_bench.agents.base import ReasoningEffort
from risk_bench.agents.models import MODELS
from risk_bench.config import Settings
from risk_bench.specs import _split_effort


def test_a_spec_resolves_to_the_model_id_it_names():
    assert specs.resolve_model(spec="anthropic:claude-opus-5") == "claude-opus-5"
    assert specs.resolve_model(spec="google:gemini-3.8-flash") == "gemini-3.8-flash"


def test_a_model_outside_the_registry_still_resolves():
    # The registry is what the setup form offers and what usage reports can price, not a
    # whitelist: naming any id the provider accepts must keep working from the CLI.
    registered = {model.id for model in MODELS}
    assert "claude-opus-4-6" not in registered
    assert specs.resolve_model(spec="anthropic:claude-opus-4-6") == "claude-opus-4-6"


def test_invalid_specs_are_rejected():
    with pytest.raises(ValueError, match="Unknown provider"):
        specs.resolve_model(spec="xai:hi")
    with pytest.raises(ValueError, match="not 'random'"):
        specs.resolve_model(spec="anthropic")


def test_split_effort_only_treats_effort_words_as_a_suffix():
    assert _split_effort(spec="anthropic:claude-opus-5:medium") == ("anthropic:claude-opus-5", ReasoningEffort.MEDIUM)
    assert _split_effort(spec="openai:gpt-5.6-luna:high") == ("openai:gpt-5.6-luna", ReasoningEffort.HIGH)
    assert _split_effort(spec="openai:gpt-5.6-luna") == ("openai:gpt-5.6-luna", None)
    assert _split_effort(spec="random") == ("random", None)


def test_effort_suffix_is_stripped_when_resolving_the_model():
    assert specs.resolve_model(spec="anthropic:claude-opus-5:low") == "claude-opus-5"
    assert specs.resolve_model(spec="openai:gpt-5.6-luna:high") == "gpt-5.6-luna"


def test_build_agent_uses_the_spec_effort_and_otherwise_the_default():
    settings = Settings(anthropic_api_key="unused", agent_reasoning_effort=ReasoningEffort.LOW)

    chosen = specs.build_agent(
        spec="anthropic:claude-opus-5:high", settings=settings, max_tokens=settings.max_agent_tokens
    )
    assert isinstance(chosen, AnthropicAgent)
    assert chosen._reasoning_effort is ReasoningEffort.HIGH

    default = specs.build_agent(
        spec="anthropic:claude-opus-5", settings=settings, max_tokens=settings.max_agent_tokens
    )
    assert isinstance(default, AnthropicAgent)
    assert default._reasoning_effort is ReasoningEffort.LOW


def test_player_names_use_resolved_model_ids_with_duplicate_suffixes():
    named = specs.named_specs(specs=["anthropic:claude-haiku-4-5", "anthropic:claude-haiku-4-5", "random"])

    assert named == {
        "claude-haiku-4-5": "anthropic:claude-haiku-4-5",
        "claude-haiku-4-5#2": "anthropic:claude-haiku-4-5",
        "random": "random",
    }
