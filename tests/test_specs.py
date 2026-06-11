import pytest

from risk_bench import specs
from risk_bench.agents.models import AnthropicModel, GoogleModel, OpenAIModel


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


def test_player_names_use_resolved_model_ids_with_duplicate_suffixes():
    named = specs.named_specs(specs=["anthropic:lo", "anthropic:lo", "random"])

    assert named == {
        "claude-haiku-4-5": "anthropic:lo",
        "claude-haiku-4-5#2": "anthropic:lo",
        "random": "random",
    }
