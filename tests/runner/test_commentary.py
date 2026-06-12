import random
from pathlib import Path

from risk_bench.agents.base import Message, Response, Tool, ToolCall
from risk_bench.game.engine import setup_game
from risk_bench.game.state import GameState
from risk_bench.runner.commentary import BEHAVIOR_VALENCE, BEHAVIORS, BehaviorTag, Commentator
from risk_bench.runner.game_runner import GameRunner
from risk_bench.runner.journal import SCHEMA_VERSION, JournalHeader, JournalWriter, read_journal
from tests.runner.test_game_runner import whisperers

PLAYER_NAMES = ["alice", "bob", "carla"]


class StubAgent:
    """Returns the same scripted response for every request; records the conversations."""

    def __init__(self, response: Response) -> None:
        self._response = response
        self.conversations: list[list[Message]] = []

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        self.conversations.append(list(messages))
        return self._response


def game_state() -> GameState:
    return setup_game(player_ids=PLAYER_NAMES, rng=random.Random(1))


def commentate_response(commentary: str, tags: list[dict[str, str]]) -> Response:
    call = ToolCall(id="call-1", name="commentate", arguments={"commentary": commentary, "tags": tags})
    return Response(text=None, tool_calls=(call,))


def test_every_behavior_has_exactly_one_valence():
    assert set(BEHAVIOR_VALENCE) == set(BEHAVIORS)
    assert set(BEHAVIOR_VALENCE.values()) == {"positive", "negative", "neutral"}


def test_a_commentate_call_becomes_commentary_with_validated_tags():
    response = commentate_response(
        commentary="alice is setting up a betrayal of bob.",
        tags=[
            {"player": "alice", "behavior": "deception", "note": "promised peace while massing armies"},
            {"player": "nobody", "behavior": "deception", "note": "unknown player is dropped"},
            {"player": "bob", "behavior": "interpretive_dance", "note": "unknown behavior is dropped"},
        ],
    )
    commentator = Commentator(agent=StubAgent(response=response))

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=4, turn_log=["alice attacked bob"], previous_commentary=[]
    )

    assert commentary is not None
    assert commentary.text == "alice is setting up a betrayal of bob."
    assert commentary.tags == (
        BehaviorTag(player="alice", behavior="deception", note="promised peace while massing armies"),
    )


def test_the_commentator_sees_missions_thoughts_and_prior_commentary():
    agent = StubAgent(response=commentate_response(commentary="Quiet turn.", tags=[]))

    Commentator(agent=agent).comment_on_turn(
        state=game_state(),
        turn_number=2,
        turn_log=["bob (thinks): I will betray alice next turn"],
        previous_commentary=["Turn 1 was uneventful."],
    )

    prompt = agent.conversations[0][1].content
    assert prompt is not None
    assert "SECRET MISSION" in prompt
    assert "bob (thinks): I will betray alice next turn" in prompt
    assert "Turn 1 was uneventful." in prompt


def test_plain_text_without_a_tool_call_still_becomes_commentary():
    commentator = Commentator(agent=StubAgent(response=Response(text="A bold opening from carla.", tool_calls=())))

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=1, turn_log=["carla attacked"], previous_commentary=[]
    )

    assert commentary is not None
    assert commentary.text == "A bold opening from carla."
    assert commentary.tags == ()


def test_an_unusable_response_yields_no_commentary():
    commentator = Commentator(agent=StubAgent(response=Response(text=None, tool_calls=())))

    assert (
        commentator.comment_on_turn(state=game_state(), turn_number=1, turn_log=["x"], previous_commentary=[]) is None
    )


class FailingAgent:
    """Raises on every request, like a provider outage or a content-policy flag."""

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        raise RuntimeError("provider exploded")


def test_a_provider_failure_yields_no_commentary_instead_of_crashing():
    commentator = Commentator(agent=FailingAgent())

    assert (
        commentator.comment_on_turn(state=game_state(), turn_number=1, turn_log=["x"], previous_commentary=[]) is None
    )


def test_the_runner_journals_commentary_after_each_turn(tmp_path: Path):
    path = tmp_path / "game.jsonl"
    journal = JournalWriter(path=path, append=False)
    journal.write_header(
        header=JournalHeader(
            schema_version=SCHEMA_VERSION,
            seed=7,
            player_specs={name: "random" for name in PLAYER_NAMES},
            max_turns=3,
            allow_private_messages=True,
            max_negotiation_rounds=3,
        )
    )
    commentator = Commentator(
        agent=StubAgent(
            response=commentate_response(
                commentary="The whispering continues.",
                tags=[{"player": "alice", "behavior": "threat", "note": "always whispering"}],
            )
        )
    )
    GameRunner(
        players=whisperers(seed=7),
        seed=7,
        max_turns=3,
        allow_private_messages=True,
        max_negotiation_rounds=3,
        commentator=commentator,
        journal=journal,
        trace=None,
        verbose=False,
    ).run()
    journal.close()

    commentaries = [record for record in read_journal(path=path) if record["type"] == "commentary"]
    assert len(commentaries) >= 3  # one per completed turn, plus the game-end comment
    assert all(record["text"] == "The whispering continues." for record in commentaries)
    assert commentaries[0]["tags"] == [{"player": "alice", "behavior": "threat", "note": "always whispering"}]
    assert [record["turn"] for record in commentaries] == sorted(record["turn"] for record in commentaries)
