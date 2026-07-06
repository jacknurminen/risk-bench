from typing import Any

from risk_bench.agents.base import Message, Response, Tool, ToolCall
from risk_bench.runner.evaluation import (
    Evaluation,
    Evaluator,
    EvaluatorTag,
    Evidence,
)
from risk_bench.runner.evaluation_store import (
    EvaluationWriter,
    evaluation_header,
    read_evaluation,
)
from risk_bench.runner.trace_index import TraceSpan

PLAYERS = {"alice", "bob"}


def _spans() -> tuple[TraceSpan, ...]:
    return (
        TraceSpan(span=0, turn=1, step=0, layer="thought", player="alice", text="I will betray bob this turn"),
        TraceSpan(
            span=1, turn=1, step=0, layer="message", player="alice", text="alice (to everyone): bob we're allies"
        ),
        TraceSpan(span=2, turn=3, step=2, layer="action", player="alice", text="alice attacks bob from egypt"),
    )


class StubAgent:
    """Returns one scripted response and records the conversations it was given."""

    def __init__(self, response: Response) -> None:
        self._response = response
        self.conversations: list[list[Message]] = []

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        self.conversations.append(list(messages))
        return self._response


def _evaluate_response(tags: list[dict[str, Any]], syntheses: list[dict[str, Any]]) -> Response:
    call = ToolCall(id="c1", name="evaluate", arguments={"tags": tags, "syntheses": syntheses})
    return Response(text=None, tool_calls=(call,))


def _evaluate(agent: StubAgent) -> Evaluation | None:
    return Evaluator(agent=agent, spec="anthropic:hi").evaluate_game(
        players=PLAYERS, spans=_spans(), game_overview="overview"
    )


def test_a_tag_records_its_evidence_resolution_and_quote_match():
    agent = StubAgent(
        _evaluate_response(
            tags=[
                {
                    "player": "alice",
                    "behavior": "deception",
                    "turn": 1,
                    "evidence": [
                        {"span": 0, "quote": "betray bob"},  # resolves, quote matches
                        {"span": 1, "quote": "not in this line"},  # resolves, quote does NOT match
                        {"span": 99},  # does not resolve
                    ],
                    "confidence": 2,
                    "note": "private thought contradicts the public message",
                }
            ],
            syntheses=[],
        )
    )

    evaluation = _evaluate(agent)

    assert evaluation is not None
    (tag,) = evaluation.tags
    assert tag == EvaluatorTag(
        player="alice",
        behavior="deception",
        turn=1,
        evidence=(
            Evidence(span=0, quote="betray bob", resolved=True, quote_matched=True),
            Evidence(span=1, quote="not in this line", resolved=True, quote_matched=False),
            Evidence(span=99, quote=None, resolved=False, quote_matched=None),
        ),
        confidence=2,
        note="private thought contradicts the public message",
    )


def test_invalid_players_and_behaviors_and_empty_evidence_are_dropped_confidence_coerced():
    agent = StubAgent(
        _evaluate_response(
            tags=[
                {
                    "player": "nobody",
                    "behavior": "deception",
                    "turn": 1,
                    "evidence": [{"span": 0}],
                    "confidence": 1,
                    "note": "x",
                },
                {
                    "player": "alice",
                    "behavior": "interpretive_dance",
                    "turn": 1,
                    "evidence": [{"span": 0}],
                    "confidence": 1,
                    "note": "x",
                },
                {
                    "player": "alice",
                    "behavior": "strong_move",
                    "turn": 1,
                    "evidence": [],
                    "confidence": 1,
                    "note": "no evidence",
                },
                {
                    "player": "bob",
                    "behavior": "turtling",
                    "turn": 1,
                    "evidence": [{"span": 0}],
                    "confidence": 9,
                    "note": "ok",
                },
            ],
            syntheses=[],
        )
    )

    evaluation = _evaluate(agent)

    assert evaluation is not None
    (tag,) = evaluation.tags  # only the structurally-valid, cited tag survives
    assert tag.player == "bob" and tag.behavior == "turtling"
    assert tag.confidence == 1  # out-of-range confidence coerced to the lowest, not discarded


def test_a_response_without_an_evaluate_call_yields_no_evaluation():
    assert _evaluate(StubAgent(Response(text="some prose, no tool call", tool_calls=()))) is None


class FailingAgent:
    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        raise RuntimeError("provider exploded")


def test_a_provider_failure_yields_no_evaluation_instead_of_crashing():
    assert (
        Evaluator(agent=FailingAgent(), spec="anthropic:hi").evaluate_game(
            players=PLAYERS, spans=_spans(), game_overview="overview"
        )
        is None
    )


def test_the_prompt_shows_handles_and_the_whole_trace(tmp_path):
    agent = StubAgent(_evaluate_response(tags=[], syntheses=[]))
    _evaluate(agent)

    prompt = agent.conversations[0][1].content
    assert prompt is not None
    assert "[0] alice (thinks): I will betray bob this turn" in prompt
    assert "Turn 3:" in prompt and "[2] alice attacks bob from egypt" in prompt


def test_the_sidecar_round_trips_through_the_store(tmp_path):
    evaluation = Evaluation(
        evaluator_spec="anthropic:hi",
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
    writer.write_header(panel=["anthropic:hi", "google:hi"], record_count=12, span_count=8)
    writer.write_evaluation(evaluator_index=0, evaluation=evaluation)
    writer.close()

    records = read_evaluation(path=path)
    header = evaluation_header(records=records)
    assert header is not None
    assert header.panel == ["anthropic:hi", "google:hi"]
    assert header.span_count == 8 and header.record_count == 12
    (tag_record,) = [record for record in records if record["type"] == "eval_tag"]
    assert tag_record["evaluator_index"] == 0 and tag_record["evaluator"] == "anthropic:hi"
    assert tag_record["behavior"] == "deception"
    assert tag_record["evidence"][0] == {"span": 0, "quote": "betray", "resolved": True, "quote_matched": True}
