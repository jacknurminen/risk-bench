import json
import re
from typing import Any

from risk_bench.agents.base import Message, Response, Tool, ToolCall
from risk_bench.runner.evaluation import (
    EvaluatedGame,
    Evaluation,
    Evaluator,
    EvaluatorTag,
    Evidence,
    TurnCoverage,
)
from risk_bench.runner.evaluation_store import EvaluationWriter, evaluation_header, read_evaluation
from risk_bench.runner.trace_index import TraceSpan

PLAYERS = {"alice", "bob"}
# bob is eliminated by turn 3, so turn 3 has only alice to account for
LIVING = {1: frozenset({"alice", "bob"}), 3: frozenset({"alice"})}


def _spans() -> tuple[TraceSpan, ...]:
    return (
        TraceSpan(span=0, turn=1, step=0, layer="thought", player="alice", text="I will betray bob this turn"),
        TraceSpan(
            span=1, turn=1, step=0, layer="message", player="alice", text="alice (to everyone): bob we're allies"
        ),
        TraceSpan(span=2, turn=3, step=2, layer="action", player="alice", text="alice attacks bob from egypt"),
    )


class ScriptedAgent:
    """Answers each per-turn call by the turn named in its user message, and the synthesis call by tool.

    A scripted value may be an ``Exception`` to simulate a provider failure on that specific call.
    """

    def __init__(self, turn_calls: dict[int, Response | Exception], synthesis: Response | Exception) -> None:
        self._turn_calls = turn_calls
        self._synthesis = synthesis
        self.conversations: list[list[Message]] = []
        self.tools_seen: list[str | None] = []

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        self.conversations.append(list(messages))
        tool_name = tools[0].name if tools else None
        self.tools_seen.append(tool_name)
        scripted = self._synthesis if tool_name == "synthesize" else self._turn_calls.get(_turn_of(messages), _SILENT)
        if isinstance(scripted, Exception):
            raise scripted
        return scripted


_SILENT = Response(text="some prose, no tool call", tool_calls=())


def _turn_of(messages: list[Message]) -> int:
    match = re.search(r"TURN (\d+)", messages[-1].content or "")
    return int(match.group(1)) if match else -1


def _turn_response(turn: int, tags: list[dict[str, Any]], no_behaviour: list[str]) -> Response:
    call = ToolCall(id="t", name="evaluate_turn", arguments={"turn": turn, "tags": tags, "no_behaviour": no_behaviour})
    return Response(text=None, tool_calls=(call,))


def _synthesis_response(syntheses: list[dict[str, Any]]) -> Response:
    return Response(text=None, tool_calls=(ToolCall(id="s", name="synthesize", arguments={"syntheses": syntheses}),))


def _evaluate(agent: ScriptedAgent) -> EvaluatedGame | None:
    return Evaluator(agent=agent, spec="anthropic:claude-opus-5").evaluate_game(
        players=PLAYERS, spans=_spans(), game_overview="overview", living_by_turn=LIVING
    )


def test_per_turn_tags_are_stamped_with_the_call_turn_and_assembled():
    agent = ScriptedAgent(
        turn_calls={
            1: _turn_response(
                turn=1,
                tags=[
                    {
                        "player": "alice",
                        "behavior": "deception",
                        "evidence": [{"span": 0, "quote": "betray bob"}, {"span": 1}],
                        "confidence": 2,
                        "note": "gap",
                    }
                ],
                no_behaviour=["bob"],
            ),
            3: _turn_response(
                turn=3,
                tags=[
                    {
                        "player": "alice",
                        "behavior": "strong_move",
                        "evidence": [{"span": 2}],
                        "confidence": 3,
                        "note": "good",
                    }
                ],
                no_behaviour=[],
            ),
        },
        synthesis=_synthesis_response(
            syntheses=[{"player": "alice", "profile": [], "key_episodes": [], "summary": "x"}]
        ),
    )

    result = _evaluate(agent)

    assert result is not None
    # tags carry the turn of the call that produced them, not any field the model supplied
    by_behavior = {tag.behavior: tag for tag in result.evaluation.tags}
    assert by_behavior["deception"].turn == 1 and by_behavior["strong_move"].turn == 3
    assert by_behavior["deception"].evidence == (
        Evidence(span=0, quote="betray bob", resolved=True, quote_matched=True),
        Evidence(span=1, quote=None, resolved=True, quote_matched=None),
    )
    assert len(result.evaluation.syntheses) == 1
    # every living player that turn was accounted for, so nothing is missing
    coverage = {cov.turn: cov for cov in result.coverage}
    assert coverage[1] == TurnCoverage(
        turn=1, living=("alice", "bob"), tagged=("alice",), no_behaviour=("bob",), missing=()
    )
    assert coverage[3] == TurnCoverage(turn=3, living=("alice",), tagged=("alice",), no_behaviour=(), missing=())


def test_a_living_player_neither_tagged_nor_no_opped_is_recorded_missing():
    agent = ScriptedAgent(
        turn_calls={1: _turn_response(turn=1, tags=[], no_behaviour=["alice"])},  # bob accounted for by neither
        synthesis=_synthesis_response(syntheses=[]),
    )

    result = _evaluate(agent)

    assert result is not None
    coverage = {cov.turn: cov for cov in result.coverage}
    assert coverage[1].missing == ("bob",)


def test_no_behaviour_ids_must_be_real_players():
    agent = ScriptedAgent(
        turn_calls={1: _turn_response(turn=1, tags=[], no_behaviour=["alice", "ghost", "bob"])},
        synthesis=_synthesis_response(syntheses=[]),
    )

    result = _evaluate(agent)

    assert result is not None
    # 'ghost' is not a player, so it is dropped; alice and bob remain accounted for
    assert {cov.turn: cov for cov in result.coverage}[1].no_behaviour == ("alice", "bob")


def test_a_failed_turn_call_marks_its_living_players_missing_but_other_turns_survive():
    agent = ScriptedAgent(
        turn_calls={
            1: RuntimeError("provider exploded on turn 1"),
            3: _turn_response(
                turn=3,
                tags=[
                    {
                        "player": "alice",
                        "behavior": "turtling",
                        "evidence": [{"span": 2}],
                        "confidence": 1,
                        "note": "x",
                    }
                ],
                no_behaviour=[],
            ),
        },
        synthesis=_synthesis_response(syntheses=[]),
    )

    result = _evaluate(agent)

    assert result is not None  # the member still produced turn 3 and the synthesis call
    coverage = {cov.turn: cov for cov in result.coverage}
    assert coverage[1] == TurnCoverage(
        turn=1, living=("alice", "bob"), tagged=(), no_behaviour=(), missing=("alice", "bob")
    )
    assert [tag.behavior for tag in result.evaluation.tags] == ["turtling"]


def test_invalid_players_behaviors_and_empty_evidence_are_dropped_and_confidence_coerced():
    agent = ScriptedAgent(
        turn_calls={
            1: _turn_response(
                turn=1,
                tags=[
                    {
                        "player": "nobody",
                        "behavior": "deception",
                        "evidence": [{"span": 0}],
                        "confidence": 1,
                        "note": "x",
                    },
                    {
                        "player": "alice",
                        "behavior": "interpretive_dance",
                        "evidence": [{"span": 0}],
                        "confidence": 1,
                        "note": "x",
                    },
                    {"player": "alice", "behavior": "strong_move", "evidence": [], "confidence": 1, "note": "none"},
                    {
                        "player": "bob",
                        "behavior": "turtling",
                        "evidence": [{"span": 0}],
                        "confidence": 9,
                        "note": "ok",
                    },
                ],
                no_behaviour=["alice"],
            )
        },
        synthesis=_synthesis_response(syntheses=[]),
    )

    result = _evaluate(agent)

    assert result is not None
    (tag,) = result.evaluation.tags  # only the structurally-valid, cited tag survives
    assert tag.player == "bob" and tag.behavior == "turtling"
    assert tag.confidence == 1  # out-of-range confidence coerced to the lowest, not discarded


def test_all_calls_failing_yields_no_evaluation():
    agent = ScriptedAgent(
        turn_calls={1: RuntimeError("down"), 3: RuntimeError("down")},
        synthesis=RuntimeError("down"),
    )
    assert _evaluate(agent) is None


def test_the_whole_game_trace_is_in_the_cacheable_system_prefix():
    agent = ScriptedAgent(turn_calls={}, synthesis=_synthesis_response(syntheses=[]))
    _evaluate(agent)

    system_prefix = agent.conversations[0][0].content  # index 0 is the cached system block
    assert system_prefix is not None
    assert "[0] alice (thinks): I will betray bob this turn" in system_prefix
    assert "Turn 3:" in system_prefix and "[2] alice attacks bob from egypt" in system_prefix
    # the per-turn instruction (index 1) is the only volatile part, so the prefix stays cacheable
    assert "TURN 1" in (agent.conversations[0][1].content or "")
    # the last call is the synthesis pass, offered its own tool
    assert agent.tools_seen[-1] == "synthesize"


def test_the_sidecar_round_trips_tags_and_coverage_through_the_store(tmp_path):
    evaluation = Evaluation(
        evaluator_spec="anthropic:claude-opus-5",
        tags=(
            EvaluatorTag(
                player="alice",
                behavior="deception",
                turn=1,
                evidence=(Evidence(span=0, quote="betray", resolved=True, quote_matched=True),),
                confidence=2,
                note="gap",
            ),
        ),
        syntheses=(),
    )
    path = tmp_path / "game.eval.jsonl"
    writer = EvaluationWriter(path=path)
    writer.write_header(
        panel=["anthropic:claude-opus-5", "google:gemini-3.1-pro-preview"], record_count=12, span_count=8
    )
    writer.write_evaluation(evaluator_index=0, evaluation=evaluation)
    writer.write_coverage(
        evaluator_index=0,
        coverage=TurnCoverage(turn=1, living=("alice", "bob"), tagged=("alice",), no_behaviour=("bob",), missing=()),
    )
    writer.close()

    records = read_evaluation(path=path)
    header = evaluation_header(records=records)
    assert header is not None and header.schema_version == 2
    assert re.fullmatch(r"\d{8}-\d{6}", header.run_id)  # a sortable per-second run id stamps the header
    (coverage_record,) = [record for record in records if record["type"] == "eval_coverage"]
    assert coverage_record == {
        "type": "eval_coverage",
        "evaluator_index": 0,
        "turn": 1,
        "living": ["alice", "bob"],
        "tagged": ["alice"],
        "no_behaviour": ["bob"],
        "missing": [],
    }


def test_a_synthesis_delivered_as_a_json_string_is_still_read():
    # Providers hand back the raw string when their own accumulation of a large tool argument
    # fails to parse; a stray NUL escape has been seen in exactly that payload. Dropping it
    # silently loses the whole per-player analysis of an expensive call.
    syntheses = [
        {
            "player": "alice",
            "profile": [
                {"dimension": "diplomacy", "characterization": "blunt", "evidence": [{"span": 1, "quote": "allies"}]}
            ],
            "key_episodes": [{"span": 2}],
            "summary": "held the line",
        }
    ]
    # the escape lands between a comma and the next key, exactly where the corrupt payload carried it
    payload = json.dumps(syntheses).replace('{"span": 1, "quote"', '{"span": 1,\\u0000"quote"')
    agent = ScriptedAgent(
        turn_calls={},
        synthesis=Response(
            text=None, tool_calls=(ToolCall(id="s", name="synthesize", arguments={"syntheses": payload}),)
        ),
    )

    result = _evaluate(agent)

    assert result is not None
    assert [synthesis.player for synthesis in result.evaluation.syntheses] == ["alice"]
    synthesis = result.evaluation.syntheses[0]
    assert synthesis.summary == "held the line"
    assert synthesis.profile[0].evidence == (Evidence(span=1, quote="allies", resolved=True, quote_matched=True),)
    assert synthesis.key_episodes == (Evidence(span=2, quote=None, resolved=True, quote_matched=None),)
