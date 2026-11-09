from dataclasses import replace

import pytest

from risk_bench.agents.base import Response
from risk_bench.players.profiles import ExperimentPlayer, load_profile
from risk_bench.players.rendering import GAME_RULES, render_system_prompt
from risk_bench.runner import checkpoint
from tests.players.test_llm import VALID_ATTACK_RESPONSE, StubAgent, llm_player, tool_call
from tests.players.test_tools import attack_request


@pytest.mark.parametrize("profile_id", ["mission_prober", "deceiver", "gaslighter"])
@pytest.mark.parametrize("operation", ["action", "negotiation", "notes"])
def test_profile_replaces_the_objective_for_every_model_call(profile_id, operation):
    profile = load_profile(profile_id=profile_id)
    agent = StubAgent(
        responses=[
            VALID_ATTACK_RESPONSE,
            Response(text=None, tool_calls=(tool_call("say_nothing"),)),
            Response(text="The experiment is still in progress."),
        ][["action", "negotiation", "notes"].index(operation) :]
    )
    player = llm_player(agent=agent)
    player.experiment_profile = profile.model_dump()
    view = attack_request().view
    if operation == "action":
        player.decide_action(request=attack_request())
    elif operation == "negotiation":
        player.compose_messages(view=view)
    else:
        player.update_notes(view=view)

    system = agent.conversations[0][0].content
    assert profile.objective in system
    assert profile.instructions in system
    assert GAME_RULES in system
    assert "YOUR SECRET MISSION:" in system
    assert "private_memo" in system
    assert "Your objective is to win" in system
    normal = render_system_prompt(view=replace(view, player_id="bob"))
    assert "Your objective is to win" in normal
    assert profile.objective not in normal
    assert profile.instructions not in normal


def test_profile_snapshot_survives_controller_checkpoint_restore():
    player = llm_player(agent=StubAgent(responses=[]))
    player.experiment_profile = load_profile(profile_id="mission_prober").model_dump()
    saved = checkpoint.player_state(player)
    restored = llm_player(agent=StubAgent(responses=[Response(text="Updated experimental notes.")]))

    checkpoint.restore_player(restored, saved)
    restored.update_notes(view=attack_request().view)

    assert restored.experiment_profile == player.experiment_profile
    assert player.experiment_profile["instructions"] in restored._agent.conversations[0][0].content


def test_saved_profile_identity_must_match_selection():
    with pytest.raises(ValueError, match="must match"):
        ExperimentPlayer(player=1, profile="deceiver", snapshot=load_profile(profile_id="mission_prober"))
