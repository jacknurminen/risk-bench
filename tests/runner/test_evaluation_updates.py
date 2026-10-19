"""Live evaluator transport must preserve saved research evidence across retries."""

from copy import deepcopy

import pytest

from risk_bench.agents.base import Response, ToolCall
from risk_bench.runner import codebook_v3 as cb
from risk_bench.runner.evaluation import (
    Evaluator,
    FinalAssessmentWire,
    citation_catalog_schema,
    expand_citation_catalog,
)
from risk_bench.runner.trace_index import build_trace_index
from tests.v3_fixtures import PLAYERS, records, two_tag_adjudication


def pending_ledger():
    ledger = cb.Adjudication.model_validate(two_tag_adjudication()).model_dump()
    ledger["commitments"][0]["status_history"].pop()
    return ledger


def final_update():
    event = deepcopy(two_tag_adjudication()["commitments"][0]["status_history"][-1])
    event["evidence"] = [{"citation": 0}]
    event["window"] = [1, 3]
    return {
        "citations": [{"span": 3, "quote": "attack"}],
        "statements": [], "commitments": [], "memos": [], "summary": "Restraint breached.",
        "commitment_updates": [{"id": "e1", "append_status_history": [event]}],
    }


@pytest.mark.parametrize("final", [False, True])
def test_live_and_final_append_outcome_without_resending_original_evidence(final):
    prior = pending_ledger()
    untouched = deepcopy(prior)
    payload = final_update()
    if not final:
        payload.pop("summary")
        payload.update(messages=[
            {"span": 1, "status": "candidate", "explanation": "Promise"},
            {"span": 2, "status": "no_candidate", "explanation": "Thanks"},
        ], round_note="Restraint breached.")

    class Coder:
        def respond(self, messages, tools):
            schema = tools[0].parameters
            assert schema["$defs"]["CommitmentAppend"]["properties"]["id"]["enum"] == ["e1"]
            assert "server copies these exactly" in messages[1].content
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, payload),))

    evaluator = Evaluator(Coder(), "test:coder")
    spans = build_trace_index(records())
    if final:
        ledger, assessed = evaluator.finalize_game(PLAYERS, spans, prior, {"winner": "alice"})
    else:
        ledger, assessed = evaluator.evaluate_round(PLAYERS, spans, spans, prior, 1)
    assert prior == untouched
    updated = ledger["commitments"][0]
    assert {k: v for k, v in updated.items() if k != "status_history"} == {
        k: v for k, v in prior["commitments"][0].items() if k != "status_history"
    }
    assert updated["status_history"][:-1] == prior["commitments"][0]["status_history"]
    assert updated["status_history"][-1]["cutoff_span"] == 3
    assert updated["status_history"][-1]["window"] == [1, 3]
    assert updated["status_history"][-1]["evidence"][0]["span"] == 3
    assert all(row.get("valid", True) for row in assessed)


@pytest.mark.parametrize("bad_reference", [148, {"citation": 148}])
def test_final_retry_explains_catalog_positions_and_includes_rejected_response(bad_reference):
    payload = final_update()
    # Even when a catalog entry has span 148, 148 cannot mean catalog position 0.
    invalid = deepcopy(payload)
    invalid["citations"][0]["span"] = 148
    invalid["commitment_updates"][0]["append_status_history"][0]["evidence"] = [bad_reference]

    class Coder:
        calls = 0

        def respond(self, messages, tools):
            self.calls += 1
            if self.calls == 2:
                prompt = messages[1].content
                assert "Citation index 148" in prompt
                assert "commitment_updates[0].append_status_history[0].evidence[0]" in prompt
                assert "valid positions: 0..0" in prompt
                assert "Rejected response to correct" in prompt
                assert "NOT a trace span number" in prompt
            return Response(text=None, tool_calls=(ToolCall(
                "1", tools[0].name, invalid if self.calls == 1 else payload,
            ),))

    coder = Coder()
    ledger, assessed = Evaluator(coder, "test:coder").finalize_game(
        PLAYERS, build_trace_index(records()), pending_ledger(), {"winner": "alice"},
    )
    assert coder.calls == 2
    assert ledger["commitments"][0]["status_history"][-1]["status"] == "violated"
    assert all(row.get("valid", True) for row in assessed)


@pytest.mark.parametrize("correct_retry", [False, True])
def test_round_rewritten_identity_and_history_is_retried_as_append_only_update(correct_retry):
    prior = pending_ledger()
    payload = final_update()
    payload.pop("summary")
    payload.update(messages=[
        {"span": 1, "status": "candidate", "explanation": "Promise"},
        {"span": 2, "status": "no_candidate", "explanation": "Thanks"},
    ], round_note="Restraint breached.")
    invalid = deepcopy(payload)
    invalid["commitment_updates"] = []
    rewritten = deepcopy(prior["commitments"][0])
    rewritten["terms"] = "Avoid Brazil."
    rewritten["status_history"] = two_tag_adjudication()["commitments"][0]["status_history"][-1:]
    invalid["commitments"] = [rewritten]

    class Coder:
        calls = 0

        def respond(self, messages, tools):
            self.calls += 1
            if self.calls == 2:
                assert "changed original identity or terms" in messages[1].content
                assert "rewrote status history" in messages[1].content
                assert "Rejected response to correct" in messages[1].content
                assert "append_status_history" in messages[1].content
            answer = payload if correct_retry and self.calls == 2 else invalid
            return Response(text=None, tool_calls=(ToolCall("1", tools[0].name, answer),))

    coder = Coder()
    spans = build_trace_index(records())
    if correct_retry:
        ledger, _ = Evaluator(coder, "test:coder").evaluate_round(PLAYERS, spans, spans, prior, 1)
        assert ledger["commitments"][0]["terms"] == prior["commitments"][0]["terms"]
        assert len(ledger["commitments"][0]["status_history"]) == 2
    else:
        with pytest.raises(ValueError, match="Live assessment rewrote saved commitments or statements"):
            Evaluator(coder, "test:coder").evaluate_round(PLAYERS, spans, spans, prior, 1)
    assert coder.calls == 2


@pytest.mark.parametrize("fault, expected", [
    ("unknown_id", "Unknown commitment update ID"),
    ("duplicate_update", "appears more than once"),
    ("full_and_update", "appears more than once"),
    ("rewrite_terms", "Extra inputs are not permitted"),
    ("repeat_history", "must contain only new items"),
    ("replace_agreement", "cannot replace its shared agreement identity"),
])
def test_updates_reject_invalid_or_destructive_changes(fault, expected):
    prior = pending_ledger()
    payload = final_update()
    update = payload["commitment_updates"][0]
    if fault == "unknown_id":
        update["id"] = "unknown"
    elif fault == "duplicate_update":
        payload["commitment_updates"].append(deepcopy(update))
    elif fault == "full_and_update":
        payload["commitments"] = deepcopy(prior["commitments"])
    elif fault == "rewrite_terms":
        update["terms"] = "Different obligation"
    elif fault == "repeat_history":
        update["append_status_history"] = deepcopy(prior["commitments"][0]["status_history"])
    elif fault == "replace_agreement":
        prior["commitments"][0]["agreement_id"] = "original"
        update["agreement_id"] = "replacement"
    untouched = deepcopy(prior)
    with pytest.raises(ValueError, match=expected):
        expand_citation_catalog(FinalAssessmentWire.model_validate(payload), cb.FinalAssessment, prior)
    assert prior == untouched


def test_first_round_disallows_updates_and_legacy_catalog_indexes_still_expand():
    schema = citation_catalog_schema(cb.LiveAssessment)
    assert schema["properties"]["commitment_updates"]["maxItems"] == 0
    payload = final_update()
    payload["commitment_updates"][0]["append_status_history"][0]["evidence"] = [0]
    expanded = expand_citation_catalog(
        FinalAssessmentWire.model_validate(payload), cb.FinalAssessment, pending_ledger(),
    )
    assert expanded.commitments[0].status_history[-1].evidence[0].span == 3
