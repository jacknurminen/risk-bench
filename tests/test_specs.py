import pytest

from risk_bench import specs
from risk_bench.agents.anthropic import AnthropicAgent
from risk_bench.agents.base import ReasoningEffort
from risk_bench.agents.models import AnthropicModel, GoogleModel, OpenAIModel
from risk_bench.config import Settings
from risk_bench.specs import _split_effort


def test_tier_specs_resolve_to_concrete_model_ids():
    assert specs.resolve_model(spec="anthropic:lo") == AnthropicModel.LO
    assert specs.resolve_model(spec="anthropic:mid") == "claude-sonnet-4-6"
    assert specs.resolve_model(spec="anthropic:hi") == "claude-opus-4-8"
    assert specs.resolve_model(spec="google:hi") == GoogleModel.HI
    assert specs.resolve_model(spec="openai:lo") == OpenAIModel.LO


def test_explicit_model_ids_pass_through_unchanged():
    assert specs.resolve_model(spec="anthropic:claude-opus-4-6") == "claude-opus-4-6"
    assert specs.resolve_model(spec="openai:gpt-5.4-nano") == "gpt-5.4-nano"


def test_invalid_specs_are_rejected():
    with pytest.raises(ValueError, match="Unknown provider"):
        specs.resolve_model(spec="xai:hi")
    with pytest.raises(ValueError, match="not 'random'"):
        specs.resolve_model(spec="anthropic")


def test_split_effort_only_treats_effort_words_as_a_suffix():
    assert _split_effort(spec="anthropic:hi:medium") == ("anthropic:hi", ReasoningEffort.MEDIUM)
    assert _split_effort(spec="openai:gpt-5.4-nano:high") == ("openai:gpt-5.4-nano", ReasoningEffort.HIGH)
    assert _split_effort(spec="anthropic:hi") == ("anthropic:hi", None)  # 'hi' is a tier, not an effort
    assert _split_effort(spec="openai:gpt-5.4-nano") == ("openai:gpt-5.4-nano", None)
    assert _split_effort(spec="random") == ("random", None)


def test_effort_suffix_is_stripped_when_resolving_the_model():
    assert specs.resolve_model(spec="anthropic:hi:low") == "claude-opus-4-8"
    assert specs.resolve_model(spec="openai:gpt-5.4-nano:high") == "gpt-5.4-nano"


def test_build_agent_uses_the_spec_effort_and_otherwise_the_default():
    settings = Settings(anthropic_api_key="unused", agent_reasoning_effort=ReasoningEffort.LOW)

    chosen = specs.build_agent(spec="anthropic:hi:high", settings=settings, max_tokens=settings.max_agent_tokens)
    assert isinstance(chosen, AnthropicAgent)
    assert chosen._reasoning_effort is ReasoningEffort.HIGH

    default = specs.build_agent(spec="anthropic:hi", settings=settings, max_tokens=settings.max_agent_tokens)
    assert isinstance(default, AnthropicAgent)
    assert default._reasoning_effort is ReasoningEffort.LOW


def test_player_names_use_resolved_model_ids_with_duplicate_suffixes():
    named = specs.named_specs(specs=["anthropic:lo", "anthropic:lo", "random"])

    assert named == {
        "claude-haiku-4-5": "anthropic:lo",
        "claude-haiku-4-5#2": "anthropic:lo",
        "random": "random",
    }
