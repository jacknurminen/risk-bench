import pytest
import typer

from risk_bench.agents.models import AnthropicModel, GoogleModel, OpenAIModel
from risk_bench.config import Settings
from risk_bench.main import _named_factories, _resolve_model


def test_tier_specs_resolve_to_concrete_model_ids():
    assert _resolve_model(spec="anthropic:lo") == AnthropicModel.LO
    assert _resolve_model(spec="anthropic:mid") == "claude-sonnet-4-6"
    assert _resolve_model(spec="anthropic:hi") == "claude-opus-4-8"
    assert _resolve_model(spec="google:hi") == GoogleModel.HI
    assert _resolve_model(spec="openai:lo") == OpenAIModel.LO


def test_explicit_model_ids_pass_through_unchanged():
    assert _resolve_model(spec="anthropic:claude-opus-4-6") == "claude-opus-4-6"
    assert _resolve_model(spec="openai:gpt-5.4-nano") == "gpt-5.4-nano"


def test_invalid_specs_are_rejected():
    with pytest.raises(typer.BadParameter, match="Unknown provider"):
        _resolve_model(spec="xai:hi")
    with pytest.raises(typer.BadParameter, match="not 'random'"):
        _resolve_model(spec="anthropic")


def test_player_names_use_resolved_model_ids_with_duplicate_suffixes():
    settings = Settings(anthropic_api_key="test-key")
    factories = _named_factories(specs=["anthropic:lo", "anthropic:lo", "random"], settings=settings)

    assert list(factories) == ["claude-haiku-4-5", "claude-haiku-4-5#2", "random"]
