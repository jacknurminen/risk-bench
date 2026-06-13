import random
from pathlib import Path

from risk_bench.agents.base import Message, Response, Tool, ToolCall
from risk_bench.game.engine import setup_game
from risk_bench.game.state import GameState
from risk_bench.runner.commentary import (
    BEHAVIOR_AXIS,
    BEHAVIORS,
    CONDUCT_RUBRIC,
    CONDUCT_VALENCE,
    SKILL_RUBRIC,
    SKILL_VALENCE,
    BehaviorTag,
    Commentator,
    axis_and_valence,
)
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


def test_every_behavior_sits_on_exactly_one_axis_with_a_consistent_valence():
    assert set(BEHAVIOR_AXIS) == set(BEHAVIORS)
    assert set(BEHAVIOR_AXIS.values()) == {"skill", "conduct", "affect"}
    # the two rubrics partition the whole vocabulary, with no overlap
    assert set(SKILL_RUBRIC.behaviors) | set(CONDUCT_RUBRIC.behaviors) == set(BEHAVIORS)
    assert not set(SKILL_RUBRIC.behaviors) & set(CONDUCT_RUBRIC.behaviors)
    for behavior in BEHAVIORS:
        axis, valence = axis_and_valence(behavior)
        if axis == "skill":
            assert valence in {"strong", "weak"}
            assert SKILL_VALENCE[behavior] == valence
        elif axis == "conduct":
            assert valence in {"cooperative", "adversarial", "deceptive"}
            assert CONDUCT_VALENCE[behavior] == valence
        else:
            assert valence is None  # affect is descriptive, never scored


def test_a_commentator_ignores_tags_outside_its_rubric():
    response = commentate_response(
        commentary="alice blundered while bob schemed.",
        tags=[
            {"player": "alice", "behavior": "strategic_blunder", "note": "a skill tag, in scope"},
            {"player": "bob", "behavior": "deception", "note": "a conduct tag, out of scope here"},
        ],
    )
    commentator = Commentator(agent=StubAgent(response=response), rubric=SKILL_RUBRIC)

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=2, turn_log=["alice attacked"], previous_commentary=[]
    )

    assert commentary is not None
    assert commentary.tags == (
        BehaviorTag(player="alice", behavior="strategic_blunder", note="a skill tag, in scope"),
    )


def test_a_commentate_call_becomes_commentary_with_validated_tags():
    response = commentate_response(
        commentary="alice is setting up a betrayal of bob.",
        tags=[
            {"player": "alice", "behavior": "deception", "note": "promised peace while massing armies"},
            {"player": "nobody", "behavior": "deception", "note": "unknown player is dropped"},
            {"player": "bob", "behavior": "interpretive_dance", "note": "unknown behavior is dropped"},
        ],
    )
    commentator = Commentator(agent=StubAgent(response=response), rubric=CONDUCT_RUBRIC)

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

    Commentator(agent=agent, rubric=CONDUCT_RUBRIC).comment_on_turn(
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
    commentator = Commentator(
        agent=StubAgent(response=Response(text="A bold opening from carla.", tool_calls=())), rubric=SKILL_RUBRIC
    )

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=1, turn_log=["carla attacked"], previous_commentary=[]
    )

    assert commentary is not None
    assert commentary.text == "A bold opening from carla."
    assert commentary.tags == ()


def test_a_tool_call_leaked_as_text_is_salvaged_not_shown_raw():
    leaked = (
        '<invoke name="commentate"><parameter name="commentary">alice played a strong turn.</parameter> '
        '<parameter name="tags">[{"player": "alice", "behavior": "strong_move", "note": "good odds"}]'
        "</parameter></invoke>"
    )
    commentator = Commentator(agent=StubAgent(response=Response(text=leaked, tool_calls=())), rubric=SKILL_RUBRIC)

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=1, turn_log=["alice attacked"], previous_commentary=[]
    )

    assert commentary is not None
    assert commentary.text == "alice played a strong turn."
    assert "<parameter" not in commentary.text
    assert commentary.tags == (BehaviorTag(player="alice", behavior="strong_move", note="good odds"),)


def test_a_leak_with_a_swallowed_opening_tag_keeps_prose_and_complete_tags():
    # the commonly observed shape: the text IS the commentary, then the tags parameter follows
    leaked = (
        "bob already privately decided to break the truce.</parameter>\n"
        '<parameter name="tags">[{"player": "bob", "behavior": "deception", "note": "said one thing"}, '
        '{"player": "alice", "behavior": "theory_of_mind", "note": "saw it coming and publicly wond'
    )  # cut off mid-JSON, as max-token truncation leaves it
    commentator = Commentator(agent=StubAgent(response=Response(text=leaked, tool_calls=())), rubric=CONDUCT_RUBRIC)

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=1, turn_log=["bob spoke"], previous_commentary=[]
    )

    assert commentary is not None
    assert commentary.text == "bob already privately decided to break the truce."
    # the complete leading tag survives; the truncated trailing one is dropped
    assert commentary.tags == (BehaviorTag(player="bob", behavior="deception", note="said one thing"),)


def test_leaked_markup_without_salvageable_commentary_is_discarded():
    commentator = Commentator(
        agent=StubAgent(response=Response(text='<parameter name="tags">[]</parameter>', tool_calls=())),
        rubric=SKILL_RUBRIC,
    )

    assert (
        commentator.comment_on_turn(state=game_state(), turn_number=1, turn_log=["x"], previous_commentary=[]) is None
    )


def test_an_unusable_response_yields_no_commentary():
    commentator = Commentator(agent=StubAgent(response=Response(text=None, tool_calls=())), rubric=SKILL_RUBRIC)

    assert (
        commentator.comment_on_turn(state=game_state(), turn_number=1, turn_log=["x"], previous_commentary=[]) is None
    )


class FailingAgent:
    """Raises on every request, like a provider outage or a content-policy flag."""

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        raise RuntimeError("provider exploded")


def test_a_provider_failure_yields_no_commentary_instead_of_crashing():
    commentator = Commentator(agent=FailingAgent(), rubric=SKILL_RUBRIC)

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
        ),
        rubric=CONDUCT_RUBRIC,
    )
    GameRunner(
        players=whisperers(seed=7),
        seed=7,
        max_turns=3,
        allow_private_messages=True,
        max_negotiation_rounds=3,
        commentators=(commentator,),
        journal=journal,
        trace=None,
        verbose=False,
    ).run()
    journal.close()

    commentaries = [record for record in read_journal(path=path) if record["type"] == "commentary"]
    assert len(commentaries) >= 3  # one per completed turn, plus the game-end comment
    assert all(record["text"] == "The whispering continues." for record in commentaries)
    assert all(record["rubric"] == "conduct" for record in commentaries)
    assert commentaries[0]["tags"] == [{"player": "alice", "behavior": "threat", "note": "always whispering"}]
    assert [record["turn"] for record in commentaries] == sorted(record["turn"] for record in commentaries)
