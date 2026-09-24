import json

import pytest

from risk_bench.agents.base import Response, ToolCall
from risk_bench.runner.evaluation import Evaluator
from risk_bench.runner.evaluation_store import evaluation_path_for_journal, read_evaluation
from risk_bench.runner.evaluation_versions import version_for
from risk_bench.runner.live_evaluation import run_live_evaluation
from risk_bench.runner.trace_index import build_trace_index
from risk_bench.viewer.evaluation import build_evaluation_view_model
from tests.v3_fixtures import adjudication, records


def test_turn_assessment_uses_new_spans_and_previous_ledger():
    spans = build_trace_index(records())
    turn_spans = tuple(s for s in spans if s.turn == 1)

    class Coder:
        def respond(self, messages, tools):
            assert 'Prior research ledger' in messages[0].content
            assert '"id": "e1"' in messages[0].content
            assert tools[0].parameters["properties"]["messages"]["minItems"] == 2
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, {
                **adjudication(),
                "messages": [
                    {"span": 1, "status": "candidate", "explanation": "Promise"},
                    {"span": 2, "status": "no_candidate", "explanation": "Thanks"},
                ],
                "turn_note": "Alice promised restraint; Bob acknowledged it.",
            }),))

    ledger, result = Evaluator(Coder(), "test:coder").evaluate_turn(
        {"alice", "bob", "carla"}, spans, turn_spans,
        adjudication(),
    )
    assert ledger["episodes"][0]["id"] == "e1"
    assert [r["span"] for r in result if r["type"] == "eval_message_coverage"] == [1, 2]
    assert next(r for r in result if r["type"] == "eval_live_commentary")["text"].startswith("Alice")


def test_final_assessment_keeps_the_ledger_and_uses_the_recorded_result():
    spans = build_trace_index(records())

    class Coder:
        def respond(self, messages, tools):
            assert '"winner": "alice"' in messages[0].content
            assert '"id": "e1"' in messages[0].content
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, {
                **adjudication(), "summary": "Alice won; her earlier commitment was violated."
            }),))

    ledger, result = Evaluator(Coder(), "test:coder").finalize_game(
        {"alice", "bob", "carla"}, spans, adjudication(),
        {"type": "result", "winner": "alice", "turn_count": 1},
    )
    assert ledger["episodes"][0]["id"] == "e1"
    assert next(r for r in result if r["type"] == "eval_live_final")["text"].startswith("Alice won")


class DeterministicEvaluator:
    spec = "test:coder"

    def __init__(self):
        self.calls = []
        self.fail = False
        self.final_calls = 0
        self.fail_final = False

    def evaluate_turn(self, players, spans, turn_spans, ledger):
        self.calls.append((tuple(s.span for s in turn_spans), ledger))
        if self.fail:
            raise RuntimeError("API unavailable")
        ledger = {"agreements": [], "episodes": [], "claims": [], "memos": []}
        rows = tuple(
            {"type": "eval_message_coverage", "span": s.span, "status": "no_candidate", "explanation": "None"}
            for s in turn_spans if s.layer == "message"
        ) + (
            {"type": "eval_live_commentary", "turn": turn_spans[-1].turn, "text": "No relevant commitment this turn."},
            {"type": "eval_member_status", "status": "complete", "memos": []},
        )
        return ledger, rows

    def finalize_game(self, players, spans, ledger, result):
        self.final_calls += 1
        if self.fail_final:
            raise RuntimeError("Final API call unavailable")
        return ledger or {"agreements": [], "episodes": [], "claims": [], "memos": []}, (
            {"type": "eval_live_final", "turn": spans[-1].turn, "text": "Final outcome reviewed."},
            {"type": "eval_member_status", "status": "complete", "memos": []},
        )


def test_live_evaluation_saves_completed_turn_and_resumes_after_api_failure(tmp_path, monkeypatch):
    journal = tmp_path / "game-1" / "game.jsonl"
    journal.parent.mkdir()
    journal.write_text("".join(json.dumps(row) + "\n" for row in [*records(), {"type": "result", "winner": None}]))
    evaluator = DeterministicEvaluator()
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))
    evaluator.fail = True
    with pytest.raises(RuntimeError, match="API unavailable"):
        run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert not evaluation_path_for_journal(journal).exists()

    evaluator.fail = False
    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    saved = read_evaluation(evaluation_path_for_journal(journal))
    assert saved[-1] == {"type": "eval_live_cutoff", "turn": 1, "finalized": True}
    assert {r["span"] for r in saved if r["type"] == "eval_message_coverage"} == {1, 2}
    view = build_evaluation_view_model(journal, version_for(journal, None))
    assert view["live_evaluation"] and view["evaluated_through_turn"] == 1
    assert view["live_finalized"]
    assert view["live_final"][0]["text"] == "Final outcome reviewed."
    assert view["coverage"]["source_matches"]
    assert view["live_commentary"][0]["text"] == "No relevant commitment this turn."
    assert evaluator.calls[0][0] == evaluator.calls[1][0]

    # Restarting the worker with the same panel leaves the committed turn untouched.
    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert len(evaluator.calls) == 2
    assert evaluator.final_calls == 1


def test_failed_final_call_retries_without_replaying_the_last_turn(tmp_path, monkeypatch):
    journal = tmp_path / "game-final" / "game.jsonl"
    journal.parent.mkdir()
    journal.write_text("".join(json.dumps(row) + "\n" for row in [*records(), {"type": "result", "winner": None}]))
    evaluator = DeterministicEvaluator()
    evaluator.fail_final = True
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))

    with pytest.raises(RuntimeError, match="Final API call unavailable"):
        run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    saved = read_evaluation(evaluation_path_for_journal(journal))
    assert saved[-1] == {"type": "eval_live_cutoff", "turn": 1, "finalized": False}
    assert len(evaluator.calls) == 1

    evaluator.fail_final = False
    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert len(evaluator.calls) == 1
    assert evaluator.final_calls == 2
    assert read_evaluation(evaluation_path_for_journal(journal))[-1]["finalized"]


def test_live_evaluation_waits_for_the_next_turn_boundary(tmp_path, monkeypatch):
    journal = tmp_path / "game-2" / "game.jsonl"
    journal.parent.mkdir()
    rows = records()
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    evaluator = DeterministicEvaluator()
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))

    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert not evaluator.calls

    with journal.open("a") as file:
        file.write(json.dumps({"type": "note", "player": "bob", "turn": 2, "text": "Next turn"}) + "\n")
    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert len(evaluator.calls) == 1
    assert read_evaluation(evaluation_path_for_journal(journal))[-1]["turn"] == 1
