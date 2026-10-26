import pytest

from risk_bench import launch
from risk_bench.agents.base import Message, Response, Tool
from risk_bench.config import Settings
from risk_bench.players.llm import ProviderUnavailableError
from risk_bench.players.profiles import ExperimentPlayer, load_profile
from risk_bench.runner.journal import restore_game
from risk_bench.viewer.prompts import preview_next_prompts
from risk_bench.viewer.snapshots import build_view_model


class PausingAgent:
    def __init__(self) -> None:
        self.requests: list[list[Message]] = []

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        self.requests.append(messages)
        raise RuntimeError("Offline test pause")


@pytest.mark.parametrize("seat_order", [[3, 1, 2], [1, 3, 2], None])
def test_profile_stays_with_roster_player_and_survives_resume_and_preview(tmp_path, monkeypatch, seat_order):
    agent = PausingAgent()
    monkeypatch.setattr(launch.specs, "build_agent", lambda **kwargs: agent)
    path = tmp_path / "game.jsonl"
    settings = Settings(google_api_key="unused")
    profile = load_profile(profile_id="deceiver")
    selection = ExperimentPlayer(player=3, profile="deceiver", snapshot=profile)
    with pytest.raises(ProviderUnavailableError, match="Offline test pause"):
        launch.run_new_game(
            models=["google:gemini-4-argon:medium"] * 3,
            seed=123,
            settings=settings,
            commentators=[],
            journal_path=path,
            max_turns=2,
            verbose=False,
            additional_context=["normal A", "normal B", "experiment context"],
            seat_order=seat_order,
            experiment_player=selection,
        )
    restored = restore_game(path)
    recorded = restored.header.configuration["experiment_player"]
    assert recorded["player_id"] == "gemini-4-argon#3"
    assert recorded["sha256"] == profile.sha256
    viewed_players = build_view_model(journal_path=path)["players"]
    assert {player["name"]: player["experiment_profile"] for player in viewed_players} == {
        "gemini-4-argon": None,
        "gemini-4-argon#2": None,
        "gemini-4-argon#3": {"id": "deceiver", "label": "Deceiver"},
    }
    previews = preview_next_prompts(journal_path=path, additional_context={})
    for preview in previews:
        if preview.player == recorded["player_id"]:
            assert profile.instructions in preview.system_prompt
            assert "Your objective is to win" not in preview.system_prompt
            assert "experiment context" in preview.before
        else:
            assert profile.instructions not in preview.system_prompt
            assert "Your objective is to win" in preview.system_prompt
            assert "experiment context" not in preview.before

    monkeypatch.setattr(launch, "load_profile", lambda **kwargs: pytest.fail("Reloaded a frozen profile"))
    with pytest.raises(ProviderUnavailableError, match="Offline test pause"):
        launch.resume_game(journal_file=path, settings=settings, commentators=[], verbose=False)
    assert agent.requests[0][0].content == agent.requests[1][0].content
