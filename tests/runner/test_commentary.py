import random
from pathlib import Path

from risk_bench.agents.base import Message, Response, Tool, ToolCall
from risk_bench.game.engine import setup_game
from risk_bench.game.state import GameState
from risk_bench.runner.commentary import (
    BEHAVIOR_AXIS,
    BEHAVIORS,
    CONDUCT_VALENCE,
    SKILL_VALENCE,
    BehaviorTag,
    Commentator,
    axis_and_valence,
)
from risk_bench.runner.game_runner import GameRunner
from risk_bench.runner.journal import SCHEMA_VERSION, JournalHeader, JournalWriter, read_journal
from tests.runner.test_game_runner import noting_players, whisperers

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


def test_one_pass_member_tags_the_whole_vocabulary_skill_and_conduct_together():
    # a single member judges both axes in one call; a skill tag and a conduct tag both survive
    response = commentate_response(
        commentary="alice blundered into a betrayal of bob.",
        tags=[
            {"player": "alice", "behavior": "strategic_blunder", "note": "a skill tag"},
            {"player": "alice", "behavior": "betrayal", "note": "a conduct tag, same pass"},
        ],
    )
    commentator = Commentator(agent=StubAgent(response=response), spec="anthropic:hi")

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=2, turn_log=["alice attacked"], previous_commentary=[], notes={}
    )

    assert commentary is not None
    assert commentary.tags == (
        BehaviorTag(player="alice", behavior="strategic_blunder", note="a skill tag"),
        BehaviorTag(player="alice", behavior="betrayal", note="a conduct tag, same pass"),
    )


def test_a_commentate_call_validates_players_and_behaviors():
    response = commentate_response(
        commentary="alice is setting up a betrayal of bob.",
        tags=[
            {"player": "alice", "behavior": "deception", "note": "promised peace while massing armies"},
            {"player": "nobody", "behavior": "deception", "note": "unknown player is dropped"},
            {"player": "bob", "behavior": "interpretive_dance", "note": "unknown behavior is dropped"},
        ],
    )
    commentator = Commentator(agent=StubAgent(response=response), spec="anthropic:hi")

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=4, turn_log=["alice attacked bob"], previous_commentary=[], notes={}
    )

    assert commentary is not None
    assert commentary.text == "alice is setting up a betrayal of bob."
    assert commentary.tags == (
        BehaviorTag(player="alice", behavior="deception", note="promised peace while massing armies"),
    )


def test_the_commentator_sees_missions_thoughts_notes_and_prior_commentary():
    agent = StubAgent(response=commentate_response(commentary="Quiet turn.", tags=[]))

    Commentator(agent=agent, spec="anthropic:hi").comment_on_turn(
        state=game_state(),
        turn_number=2,
        turn_log=["bob (thinks): I will betray alice next turn"],
        previous_commentary=["Turn 1 was uneventful."],
        notes={"bob": "alice trusts me; strike her northern flank next turn", "alice": ""},
    )

    prompt = agent.conversations[0][1].content
    assert prompt is not None
    assert "SECRET MISSION" in prompt
    assert "bob (thinks): I will betray alice next turn" in prompt
    assert "Turn 1 was uneventful." in prompt
    assert "alice trusts me; strike her northern flank next turn" in prompt  # bob's private notes are shown
    assert "private end-of-turn notes" in prompt


def test_plain_text_without_a_tool_call_still_becomes_commentary():
    commentator = Commentator(
        agent=StubAgent(response=Response(text="A bold opening from carla.", tool_calls=())), spec="anthropic:hi"
    )

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=1, turn_log=["carla attacked"], previous_commentary=[], notes={}
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
    commentator = Commentator(agent=StubAgent(response=Response(text=leaked, tool_calls=())), spec="anthropic:hi")

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=1, turn_log=["alice attacked"], previous_commentary=[], notes={}
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
    commentator = Commentator(agent=StubAgent(response=Response(text=leaked, tool_calls=())), spec="anthropic:hi")

    commentary = commentator.comment_on_turn(
        state=game_state(), turn_number=1, turn_log=["bob spoke"], previous_commentary=[], notes={}
    )

    assert commentary is not None
    assert commentary.text == "bob already privately decided to break the truce."
    # the complete leading tag survives; the truncated trailing one is dropped
    assert commentary.tags == (BehaviorTag(player="bob", behavior="deception", note="said one thing"),)


def test_leaked_markup_without_salvageable_commentary_is_discarded():
    commentator = Commentator(
        agent=StubAgent(response=Response(text='<parameter name="tags">[]</parameter>', tool_calls=())),
        spec="anthropic:hi",
    )

    assert (
        commentator.comment_on_turn(
            state=game_state(), turn_number=1, turn_log=["x"], previous_commentary=[], notes={}
        )
        is None
    )


def test_an_unusable_response_yields_no_commentary():
    commentator = Commentator(agent=StubAgent(response=Response(text=None, tool_calls=())), spec="anthropic:hi")

    assert (
        commentator.comment_on_turn(
            state=game_state(), turn_number=1, turn_log=["x"], previous_commentary=[], notes={}
        )
        is None
    )


class FailingAgent:
    """Raises on every request, like a provider outage or a content-policy flag."""

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        raise RuntimeError("provider exploded")


def test_a_provider_failure_yields_no_commentary_instead_of_crashing():
    commentator = Commentator(agent=FailingAgent(), spec="anthropic:hi")

    assert (
        commentator.comment_on_turn(
            state=game_state(), turn_number=1, turn_log=["x"], previous_commentary=[], notes={}
        )
        is None
    )


def test_the_runner_journals_each_panel_members_commentary_after_each_turn(tmp_path: Path):
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
            commentators=["anthropic:hi", "google:hi"],
        )
    )
    panel = (
        Commentator(
            agent=StubAgent(
                response=commentate_response(
                    commentary="anthropic's read.",
                    tags=[{"player": "alice", "behavior": "threat", "note": "always whispering"}],
                )
            ),
            spec="anthropic:hi",
        ),
        Commentator(
            agent=StubAgent(response=commentate_response(commentary="google's read.", tags=[])),
            spec="google:hi",
        ),
    )
    GameRunner(
        players=whisperers(seed=7),
        seed=7,
        max_turns=3,
        allow_private_messages=True,
        max_negotiation_rounds=3,
        commentators=panel,
        max_concurrent_commentators=2,
        journal=journal,
        trace=None,
        verbose=False,
    ).run()
    journal.close()

    commentaries = [record for record in read_journal(path=path) if record["type"] == "commentary"]
    # every member annotates every completed turn, and records are kept separately (never merged)
    assert {record["commentator"] for record in commentaries} == {"anthropic:hi", "google:hi"}
    assert {record["commentator_index"] for record in commentaries} == {0, 1}
    by_index = {0: "anthropic:hi", 1: "google:hi"}
    assert all(by_index[record["commentator_index"]] == record["commentator"] for record in commentaries)
    # within each turn, members are written in panel order, so replays are stable
    for turn in {record["turn"] for record in commentaries}:
        indices = [record["commentator_index"] for record in commentaries if record["turn"] == turn]
        assert indices == sorted(indices)


def test_each_member_receives_players_fresh_notes_during_a_game():
    agents = [StubAgent(response=commentate_response(commentary="ok", tags=[])) for _ in range(2)]
    panel = tuple(Commentator(agent=agent, spec=spec) for agent, spec in zip(agents, ["anthropic:hi", "google:hi"]))
    GameRunner(
        players=dict(noting_players(seed=4)),
        seed=4,
        max_turns=4,
        allow_private_messages=True,
        max_negotiation_rounds=3,
        commentators=panel,
        max_concurrent_commentators=2,
        journal=None,
        trace=None,
        verbose=False,
    ).run()

    # NotingPlayer writes notes containing "memo"; noting runs before commentary, so every member's
    # prompt that turn carries the fresh notes
    for agent in agents:
        prompts = [conversation[1].content for conversation in agent.conversations]
        assert any(prompt is not None and "memo" in prompt for prompt in prompts)
