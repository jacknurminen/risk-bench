import copy
import json
from dataclasses import replace

import pytest

from risk_bench.agents.base import Response, ToolCall
from risk_bench.config import Settings
from risk_bench.launch import resume_game, run_new_game
from risk_bench.players.llm import ProviderUnavailableError
from risk_bench.runner.codebook_v3 import Adjudication
from risk_bench.runner.evaluation import Evaluator, board_at_cutoff, render_trace
from risk_bench.runner.evaluation_store import evaluation_path_for_journal, read_evaluation
from risk_bench.runner.evaluation_versions import list_versions, version_for
from risk_bench.runner.journal import read_journal, restore_game
from risk_bench.runner.live_evaluation import review_activity_for_journal, run_live_evaluation
from risk_bench.runner.trace_index import build_trace_index
from risk_bench.viewer.evaluation import build_evaluation_view_model
from tests.v3_fixtures import records, two_tag_adjudication


def round_records():
    return [{**row, "round": 1} if row["type"] != "header" else row for row in records()]


def with_citation_catalog(payload):
    catalog = []

    def compact(value):
        if isinstance(value, dict):
            if "span" in value and ("quote" in value or "field_path" in value):
                if value not in catalog:
                    catalog.append(value)
                return catalog.index(value)
            return {key: compact(child) for key, child in value.items()}
        if isinstance(value, list):
            return [compact(child) for child in value]
        return value

    return {**compact(payload), "citations": catalog}


def test_round_trace_omits_repeated_board_state_but_supplies_one_cutoff_snapshot():
    action = replace(build_trace_index(records())[3], data={
        "action": {"kind": "place_armies", "territory": "brazil", "count": 2},
        "state_after": {"board": {"brazil": ["alice", 2]}},
    })
    rendered = render_trace((action,))
    assert '"action"' in rendered
    assert '"state_after"' not in rendered and '"state_changes"' not in rendered
    assert board_at_cutoff((action,)).count('"board"') == 1


def test_round_assessment_uses_new_spans_and_previous_ledger():
    spans = build_trace_index(records())
    turn_spans = tuple(s for s in spans if s.turn == 1)

    class Coder:
        def respond(self, messages, tools):
            assert 'Prior research ledger' in messages[0].content
            assert '"id": "e1"' in messages[0].content
            assert tools[0].parameters["properties"]["messages"]["minItems"] == 2
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, {
                **two_tag_adjudication(),
                "messages": [
                    {"span": 1, "status": "candidate", "explanation": "Promise"},
                    {"span": 2, "status": "no_candidate", "explanation": "Thanks"},
                ],
                "round_note": "Alice promised restraint; Bob acknowledged it.",
            }),))

    ledger, result = Evaluator(Coder(), "test:coder").evaluate_round(
        {"alice", "bob", "carla"}, spans, turn_spans,
        Adjudication.model_validate(two_tag_adjudication()).model_dump(),
        1,
    )
    assert ledger["commitments"][0]["id"] == "e1"
    assert [r["span"] for r in result if r["type"] == "eval_message_coverage"] == [1, 2]
    assert next(r for r in result if r["type"] == "eval_live_commentary")["text"].startswith("Alice")


def test_round_assessment_reuses_catalog_citations_without_repeating_quotes():
    spans = build_trace_index(records())
    round_spans = tuple(s for s in spans if s.turn == 1)
    answer = with_citation_catalog(two_tag_adjudication())
    answer.update(messages=[
        {"span": 1, "status": "candidate", "explanation": "Promise"},
        {"span": 2, "status": "no_candidate", "explanation": "Thanks"},
    ], round_note="One commitment assessed.")

    class Coder:
        calls = 0

        def respond(self, messages, tools):
            self.calls += 1
            schema = tools[0].parameters
            assert schema["$defs"]["Statement"]["properties"]["passage"] == {"type": "integer", "minimum": 0}
            assert schema["properties"]["citations"]["items"] == {"$ref": "#/$defs/Citation"}
            if self.calls == 1:
                invalid = copy.deepcopy(answer)
                invalid["statements"][0]["passage"] = 99
                return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, invalid),))
            assert "Citation index 99" in messages[1].content
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, answer),))

    coder = Coder()
    ledger, result = Evaluator(coder, "test:coder").evaluate_round(
        {"alice", "bob", "carla"}, spans, round_spans, None, 1,
    )
    assert coder.calls == 2
    assert len(answer["citations"]) == 4
    assert isinstance(ledger["commitments"][0]["passage"], dict)
    assert ledger["commitments"][0]["passage"]["quote"] in spans[1].text
    assert any(row["type"] == "eval_commitment" for row in result)


def test_unchanged_units_are_retained_without_model_repeating_them():
    original = build_trace_index(records())
    next_span = replace(original[-1], span=len(original), turn=2, round=2, text="No new undertaking.")
    spans = (*original, next_span)
    prior = Adjudication.model_validate(two_tag_adjudication()).model_dump()

    class Coder:
        def respond(self, messages, tools):
            assert "return ONLY new records and existing" in messages[1].content
            assert '"id": "e1"' in messages[0].content
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, {
                "messages": [], "statements": [], "commitments": [], "memos": [],
                "round_note": "No new relevant passage.",
            }),))

    ledger, assessed = Evaluator(Coder(), "test:coder").evaluate_round(
        {"alice", "bob", "carla"}, spans, (next_span,), prior, 2,
    )
    assert ledger["statements"] == prior["statements"]
    assert ledger["commitments"] == prior["commitments"]
    assert all(row.get("valid", True) for row in assessed)


def test_round_assessment_corrects_mixed_quote_and_field_citation():
    spans = build_trace_index(records())
    round_spans = tuple(s for s in spans if s.turn == 1)

    class Coder:
        calls = 0

        def respond(self, messages, tools):
            self.calls += 1
            if self.calls == 2:
                assert "statements.0.comparison.0" in messages[1].content
                assert "Never put a quote and field_path in the same citation" in messages[1].content
            result = copy.deepcopy(two_tag_adjudication())
            if self.calls == 1:
                result["statements"][0]["comparison"] = [
                    {"span": 3, "quote": "brazil", "field_path": ["to_territory"], "field_value": "brazil"}
                ]
            result.update(messages=[
                {"span": 1, "status": "candidate", "explanation": "Promise"},
                {"span": 2, "status": "no_candidate", "explanation": "Acknowledgement"},
            ], round_note="Round reviewed")
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, result),))

    coder = Coder()
    ledger, result = Evaluator(coder, "test:coder").evaluate_round(
        {"alice", "bob", "carla"}, spans, round_spans, None, 1,
    )
    assert coder.calls == 2
    assert ledger["statements"][0]["comparison"][0]["quote"] == "I will attack Brazil."
    assert result[-1]["status"] in {"complete", "partial"}


def test_persistently_invalid_citation_still_pauses_round_review():
    spans = build_trace_index(records())
    round_spans = tuple(s for s in spans if s.turn == 1)

    class Coder:
        calls = 0

        def respond(self, messages, tools):
            self.calls += 1
            result = copy.deepcopy(two_tag_adjudication())
            result["statements"][0]["comparison"] = [
                {"span": 3, "quote": "brazil", "field_path": ["to_territory"], "field_value": "brazil"}
            ]
            result.update(messages=[
                {"span": 1, "status": "candidate", "explanation": "Promise"},
                {"span": 2, "status": "no_candidate", "explanation": "Acknowledgement"},
            ], round_note="Round reviewed")
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, result),))

    coder = Coder()
    with pytest.raises(ValueError, match="failed schema validation after correction: statements.0.comparison.0"):
        Evaluator(coder, "test:coder").evaluate_round(
            {"alice", "bob", "carla"}, spans, round_spans, None, 1,
        )
    assert coder.calls == 2


def test_final_assessment_keeps_the_ledger_and_uses_the_recorded_result():
    spans = build_trace_index(records())

    class Coder:
        def respond(self, messages, tools):
            assert '"winner": "alice"' in messages[0].content
            assert '"id": "e1"' in messages[0].content
            assert tools[0].parameters["$defs"]["Commitment"]["properties"]["passage"] == {
                "type": "integer", "minimum": 0,
            }
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, with_citation_catalog({
                **two_tag_adjudication(), "summary": "Alice won; her earlier commitment was violated."
            })),))

    ledger, result = Evaluator(Coder(), "test:coder").finalize_game(
        {"alice", "bob", "carla"}, spans, Adjudication.model_validate(two_tag_adjudication()).model_dump(),
        {"type": "result", "winner": "alice", "turn_count": 1},
    )
    assert ledger["commitments"][0]["id"] == "e1"
    assert next(r for r in result if r["type"] == "eval_live_final")["text"].startswith("Alice won")


def test_final_assessment_can_leave_unchanged_units_out_of_its_response():
    spans = build_trace_index(records())
    prior = Adjudication.model_validate(two_tag_adjudication()).model_dump()

    class Coder:
        def respond(self, messages, tools):
            assert "Return ONLY records changed" in messages[1].content
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, {
                "statements": [], "commitments": [], "memos": [], "summary": "No changed assessments.",
            }),))

    ledger, result = Evaluator(Coder(), "test:coder").finalize_game(
        {"alice", "bob", "carla"}, spans, prior, {"type": "result", "winner": "alice"},
    )
    assert ledger == prior
    assert next(row for row in result if row["type"] == "eval_live_final")["text"] == "No changed assessments."


class DeterministicEvaluator:
    spec = "test:coder"

    def __init__(self):
        self.calls = []
        self.fail = False
        self.final_calls = 0
        self.fail_final = False
        self.activity_journal = None

    def evaluate_round(self, players, spans, turn_spans, ledger, round_number):
        if self.activity_journal:
            assert review_activity_for_journal(self.activity_journal)["round"] == round_number
        self.calls.append((tuple(s.span for s in turn_spans), ledger))
        if self.fail:
            raise RuntimeError("API unavailable")
        ledger = {"statements": [], "commitments": [], "memos": []}
        rows = tuple(
            {"type": "eval_message_coverage", "span": s.span, "status": "no_candidate", "explanation": "None"}
            for s in turn_spans if s.layer == "message"
        ) + (
            {"type": "eval_live_commentary", "turn": turn_spans[-1].turn, "round": round_number,
             "text": "No relevant commitment this round."},
            {"type": "eval_member_status", "status": "complete", "memos": []},
        )
        return ledger, rows

    def finalize_game(self, players, spans, ledger, result):
        if self.activity_journal:
            assert review_activity_for_journal(self.activity_journal)["kind"] == "final"
        self.final_calls += 1
        if self.fail_final:
            raise RuntimeError("Final API call unavailable")
        return ledger or {"statements": [], "commitments": [], "memos": []}, (
            {"type": "eval_live_final", "turn": spans[-1].turn, "text": "Final outcome reviewed."},
            {"type": "eval_member_status", "status": "complete", "memos": []},
        )


def test_live_evaluation_saves_completed_turn_and_resumes_after_api_failure(tmp_path, monkeypatch):
    journal = tmp_path / "game-1" / "game.jsonl"
    journal.parent.mkdir()
    journal.write_text("".join(
        json.dumps(row) + "\n" for row in [*round_records(), {"type": "result", "winner": None}]
    ))
    evaluator = DeterministicEvaluator()
    evaluator.activity_journal = journal
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))
    evaluator.fail = True
    with pytest.raises(RuntimeError, match="API unavailable"):
        run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert not evaluation_path_for_journal(journal).exists()
    assert review_activity_for_journal(journal) is None

    evaluator.fail = False
    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert review_activity_for_journal(journal) is None
    saved = read_evaluation(evaluation_path_for_journal(journal))
    assert saved[-1] == {"type": "eval_live_cutoff", "round": 1, "turn": 1, "finalized": True}
    assert {r["span"] for r in saved if r["type"] == "eval_message_coverage"} == {1, 2}
    view = build_evaluation_view_model(journal, version_for(journal, None))
    assert view["live_evaluation"] and view["evaluated_through_turn"] == 1
    assert view["evaluated_through_round"] == 1
    assert view["live_finalized"]
    assert view["live_final"][0]["text"] == "Final outcome reviewed."
    assert view["coverage"]["source_matches"]
    assert view["live_commentary"][0]["text"] == "No relevant commitment this round."
    assert evaluator.calls[0][0] == evaluator.calls[1][0]

    # Restarting the worker with the same panel leaves the committed turn untouched.
    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert len(evaluator.calls) == 2
    assert evaluator.final_calls == 1


def test_old_turn_cadence_is_archived_before_round_review(tmp_path, monkeypatch):
    journal = tmp_path / "game" / "game.jsonl"
    journal.parent.mkdir()
    rows = [*round_records(), {"type": "result", "winner": None}]
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    old_header = {
        "type": "eval_header", "schema_version": 4, "run_id": "20260924-000000-000001",
        "created_at": "2026-09-24T00:00:00+00:00", "panel": ["test:coder"], "live": True,
        "coverage": {"record_count": len(rows), "span_count": len(build_trace_index(rows))},
        "source": {},
    }
    evaluation_path_for_journal(journal).write_text("".join(json.dumps(row) + "\n" for row in [
        old_header, {"type": "eval_live_cutoff", "turn": 1, "finalized": False},
    ]))
    evaluator = DeterministicEvaluator()
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))

    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)

    versions = list_versions(journal)
    assert len(versions) == 2
    assert versions[0].is_current and not versions[1].is_current
    assert read_evaluation(versions[1].eval_path)[0] == old_header
    assert read_evaluation(versions[0].eval_path)[0]["cadence"] == "round"


def test_failed_final_call_retries_without_replaying_the_last_turn(tmp_path, monkeypatch):
    journal = tmp_path / "game-final" / "game.jsonl"
    journal.parent.mkdir()
    journal.write_text("".join(
        json.dumps(row) + "\n" for row in [*round_records(), {"type": "result", "winner": None}]
    ))
    evaluator = DeterministicEvaluator()
    evaluator.fail_final = True
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))

    with pytest.raises(RuntimeError, match="Final API call unavailable"):
        run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert review_activity_for_journal(journal) is None
    saved = read_evaluation(evaluation_path_for_journal(journal))
    assert saved[-1] == {"type": "eval_live_cutoff", "round": 1, "turn": 1, "finalized": False}
    assert len(evaluator.calls) == 1

    evaluator.fail_final = False
    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert len(evaluator.calls) == 1
    assert evaluator.final_calls == 2
    assert read_evaluation(evaluation_path_for_journal(journal))[-1]["finalized"]


def test_live_evaluation_waits_for_the_next_round_boundary(tmp_path, monkeypatch):
    journal = tmp_path / "game-2" / "game.jsonl"
    journal.parent.mkdir()
    rows = round_records()
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    evaluator = DeterministicEvaluator()
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))

    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert not evaluator.calls
    assert review_activity_for_journal(journal) is None

    with journal.open("a") as file:
        file.write(json.dumps({"type": "note", "player": "bob", "turn": 2, "round": 2,
                               "text": "Next round"}) + "\n")
    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
    assert len(evaluator.calls) == 1
    assert review_activity_for_journal(journal) is None
    assert read_evaluation(evaluation_path_for_journal(journal))[-1]["turn"] == 1


def test_two_turns_in_one_round_use_one_evaluator_call(tmp_path, monkeypatch):
    journal = tmp_path / "multi-turn" / "game.jsonl"
    journal.parent.mkdir()
    rows = round_records()
    rows.append({"type": "message", "round": 1, "message": {
        "turn_number": 2, "sender": "carla", "visibility": "public", "recipient": None,
        "text": "I will wait too.",
    }})
    rows.append({"type": "note", "player": "bob", "turn": 3, "round": 2, "text": "Next round"})
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    evaluator = DeterministicEvaluator()
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))

    run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)

    assert len(evaluator.calls) == 1
    assert {s for s in evaluator.calls[0][0] if s in {1, 2, 5}} == {1, 2, 5}
    assert read_evaluation(evaluation_path_for_journal(journal))[-1] == {
        "type": "eval_live_cutoff", "round": 1, "turn": 2, "finalized": False,
    }


def test_failed_round_review_blocks_game_and_retries_on_resume(tmp_path, monkeypatch):
    journal = tmp_path / "game" / "game.jsonl"
    journal.parent.mkdir()
    (journal.parent / "live-evaluators.json").write_text(json.dumps({"evaluators": ["test:coder"]}))
    evaluator = DeterministicEvaluator()
    evaluator.fail = True
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))
    with pytest.raises(ProviderUnavailableError, match="Research evaluation paused: API unavailable"):
        run_new_game(["random"] * 3, 19, Settings(), [], journal, 4, False, [])
    assert restore_game(journal).checkpoint["progress"]["stage"] == "round_review"
    assert json.loads(journal.with_suffix(".eval.error.json").read_text())["detail"] == "API unavailable"

    evaluator.fail = False
    result = resume_game(journal, Settings(), [], False)
    assert result.turn_count > 0
    assert evaluator.calls[0] == evaluator.calls[1]  # same round and prior ledger on retry
    assert read_evaluation(evaluation_path_for_journal(journal))[-1]["finalized"]
    assert not journal.with_suffix(".eval.error.json").exists()


def test_game_worker_saves_round_notes_and_final_review_before_returning(tmp_path, monkeypatch):
    journal = tmp_path / "game" / "game.jsonl"
    journal.parent.mkdir()
    (journal.parent / "live-evaluators.json").write_text(json.dumps({"evaluators": ["test:coder"]}))
    evaluator = DeterministicEvaluator()
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))

    result, _ = run_new_game(["random"] * 3, 19, Settings(), [], journal, 4, False, [])

    spans = build_trace_index(read_journal(journal))
    assert result.turn_count == 5
    assert len(evaluator.calls) == 2  # rounds 1 and 2, not five turn calls
    assert {spans[span].turn for span in evaluator.calls[0][0]} >= {1, 2, 3}
    saved = read_evaluation(evaluation_path_for_journal(journal))
    assert [(row["round"], row["turn"]) for row in saved if row["type"] == "eval_live_commentary"] == [
        (1, 3), (2, 5),
    ]
    assert saved[-1] == {"type": "eval_live_cutoff", "round": 2, "turn": 5, "finalized": True}
    assert evaluator.final_calls == 1


def test_finished_game_keeps_final_review_resumable_after_provider_failure(tmp_path, monkeypatch):
    journal = tmp_path / "game" / "game.jsonl"
    journal.parent.mkdir()
    (journal.parent / "live-evaluators.json").write_text(json.dumps({"evaluators": ["test:coder"]}))
    evaluator = DeterministicEvaluator()
    evaluator.fail_final = True
    monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))

    result, _ = run_new_game(["random"] * 3, 19, Settings(), [], journal, 4, False, [])
    assert result.turn_count == 5
    assert not read_evaluation(evaluation_path_for_journal(journal))[-1]["finalized"]
    assert "Final API call unavailable" in journal.with_suffix(".eval.error.json").read_text()

    evaluator.fail_final = False
    run_live_evaluation(journal, [evaluator.spec], Settings(), follow=False)
    assert read_evaluation(evaluation_path_for_journal(journal))[-1]["finalized"]
    assert evaluator.final_calls == 2


def test_compact_ledger_survives_failed_final_call_and_matches_uninterrupted_run(tmp_path, monkeypatch):
    journals = [tmp_path / name / "game.jsonl" for name in ("uninterrupted", "resumed")]
    for journal in journals:
        journal.parent.mkdir()
        journal.write_text("".join(json.dumps(row) + "\n" for row in
                                   [*round_records(), {"type": "result", "winner": "alice"}]))

    class Coder:
        def __init__(self):
            self.round_calls = 0
            self.final_calls = 0
            self.fail = False

        def respond(self, messages, tools):
            if tools[0].name == "submit_liveassessmentwire":
                self.round_calls += 1
                answer = {**two_tag_adjudication(), "messages": [
                    {"span": 1, "status": "candidate", "explanation": "Explicit restraint."},
                    {"span": 2, "status": "no_candidate", "explanation": "Acknowledgement."},
                ], "round_note": "Restraint breached."}
            else:
                self.final_calls += 1
                if self.fail:
                    raise RuntimeError("API unavailable")
                # The full compact history must survive the saved JSON round trip.
                assert '"status": "committed"' in messages[0].content
                assert '"terms": "Do not attack Brazil this turn."' in messages[0].content
                answer = {"statements": [], "commitments": [], "memos": [], "summary": "No changed outcome."}
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, with_citation_catalog(answer)),))

    outputs = []
    for index, journal in enumerate(journals):
        coder = Coder()
        evaluator = Evaluator(coder, "test:coder")
        monkeypatch.setattr("risk_bench.runner.live_evaluation.specs.build_evaluators", lambda **kwargs: (evaluator,))
        if index:
            coder.fail = True
            with pytest.raises(RuntimeError, match="API unavailable"):
                run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
            checkpoint = read_evaluation(evaluation_path_for_journal(journal))
            assert checkpoint[-1]["finalized"] is False
            assert next(r for r in checkpoint if r["type"] == "eval_commitment")["status"] == "violated"
            coder.fail = False
        run_live_evaluation(journal, [evaluator.spec], settings=None, follow=False)
        assert coder.round_calls == 1
        assert coder.final_calls == 1 + index
        saved = read_evaluation(evaluation_path_for_journal(journal))
        assert saved[-1]["finalized"]
        outputs.append([r for r in saved if r["type"] in {"eval_statement", "eval_commitment", "eval_live_ledger"}])
    assert outputs[0] == outputs[1]
