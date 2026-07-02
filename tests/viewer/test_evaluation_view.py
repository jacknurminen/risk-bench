import json
from pathlib import Path

from risk_bench.game.actions import PlaceArmies
from risk_bench.game.events import TurnStarted
from risk_bench.game.serialization import action_to_json, event_to_json
from risk_bench.game.types import Territory
from risk_bench.runner.annotation_store import annotations_path_for_journal, write_annotation
from risk_bench.runner.evaluation import (
    Arc,
    DimensionProfile,
    Evaluation,
    EvaluatorTag,
    Evidence,
    PlayerSynthesis,
)
from risk_bench.runner.evaluation_store import EvaluationWriter, evaluation_path_for_journal
from risk_bench.viewer.evaluation import build_evaluation_view_model


def _write_journal(path: Path) -> None:
    records = [
        {
            "type": "header",
            "schema_version": 2,
            "seed": 1,
            "player_specs": {"alice": "random", "bob": "random", "carla": "random"},
            "max_turns": 10,
            "allow_private_messages": True,
            "max_negotiation_rounds": 1,
            "commentators": [],
        },
        {"type": "events", "events": [event_to_json(TurnStarted(player_id="alice", turn_number=1))]},
        {"type": "thought", "player": "alice", "text": "I will betray bob this turn"},
        {
            "type": "action",
            "player": "alice",
            "action": action_to_json(PlaceArmies(territory=Territory.BRAZIL, count=3)),
        },
        {"type": "note", "player": "alice", "turn": 1, "text": "bob is my real target"},
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def _write_evaluation(journal_path: Path) -> None:
    # span 0 = the TurnStarted event, 1 = alice's thought, 2 = the place action, 3 = alice's note
    evaluation = Evaluation(
        evaluator_spec="anthropic:hi",
        tags=(
            EvaluatorTag(
                player="alice",
                behavior="deception",
                turn=1,
                evidence=(
                    Evidence(span=1, quote="betray bob", resolved=True, quote_matched=True),
                    Evidence(span=99, quote=None, resolved=False, quote_matched=None),  # a broken citation
                ),
                confidence=2,
                note="thought contradicts the public stance",
            ),
        ),
        arcs=(
            Arc(
                kind="commitment",
                subject="alice",
                object="bob",
                antecedent=Evidence(span=1, quote=None, resolved=True, quote_matched=None),
                consequent=Evidence(span=2, quote=None, resolved=True, quote_matched=None),
                outcome="broken",
                intent="strategic_setup",
                confidence=3,
                note="promised, then moved against bob",
            ),
        ),
        syntheses=(
            PlayerSynthesis(
                player="alice",
                profile=(
                    DimensionProfile(
                        dimension="honesty",
                        characterization="deceptive opener",
                        evidence=(Evidence(span=1, quote=None, resolved=True, quote_matched=None),),
                    ),
                ),
                key_episodes=(),
                summary="alice played a deceptive game.",
            ),
        ),
    )
    writer = EvaluationWriter(path=evaluation_path_for_journal(journal_path=journal_path))
    writer.write_header(panel=["anthropic:hi"], record_count=5, span_count=4)
    writer.write_evaluation(evaluator_index=0, evaluation=evaluation)
    writer.close()


def test_view_model_resolves_citations_and_computes_aggregates(tmp_path: Path):
    journal_path = tmp_path / "game.jsonl"
    _write_journal(path=journal_path)
    _write_evaluation(journal_path=journal_path)

    view_model = build_evaluation_view_model(journal_path=journal_path)

    assert view_model["has_evaluation"] is True
    assert [player["name"] for player in view_model["players"]] == ["alice", "bob", "carla"]
    assert len(view_model["spans"]) == 4

    (member,) = view_model["members"]
    (tag,) = member["tags"]
    # a resolved citation carries the trace step (span 1 -> the thought, replay step 0) for click-through
    assert tag["evidence"][0]["resolved"] is True and tag["evidence"][0]["step"] == 0
    assert tag["evidence"][1]["resolved"] is False and tag["evidence"][1]["step"] is None

    integrity = view_model["aggregate"]["integrity"]
    assert integrity["total_citations"] == 5  # 2 tag + 2 arc ends + 1 synthesis dimension
    assert integrity["broken_citations"] == 1

    assert view_model["aggregate"]["arc_resolution"]["alice"] == {
        "arcs": 1,
        "honoured": 0,
        "broken": 1,
        "open": 0,
        "other": 0,
    }
    (agreement,) = view_model["aggregate"]["agreement"]
    assert agreement["player"] == "alice" and agreement["behavior"] == "deception" and agreement["count"] == 1


def test_view_model_folds_in_human_annotations(tmp_path: Path):
    journal_path = tmp_path / "game.jsonl"
    _write_journal(path=journal_path)
    _write_evaluation(journal_path=journal_path)  # the panel tags alice/deception on turn 1
    write_annotation(
        path=annotations_path_for_journal(journal_path=journal_path),
        annotation={
            "type": "annotation",
            "id": "h1",
            "author": "you",
            "player": "alice",
            "behavior": "deception",
            "turn": 1,
            "confidence": 3,
            "note": "I agree with the panel",
            "evidence": [{"span": 1, "quote": "betray bob"}],
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )

    view_model = build_evaluation_view_model(journal_path=journal_path)

    (annotation,) = view_model["annotations"]
    assert annotation["id"] == "h1" and annotation["human"] is True
    # the citation resolves against the live trace index (span 1 = alice's thought, replay step 0)
    assert annotation["evidence"][0]["resolved"] is True and annotation["evidence"][0]["step"] == 0
    assert annotation["evidence"][0]["quote_matched"] is True

    # the annotators roster is the LLM panel plus "you"
    assert view_model["annotators"][-1] == {"index": -1, "label": "you", "spec": None, "human": True}

    # you and the panel both tagged alice/deception/turn 1, so agreement now counts two annotators
    (agreement,) = view_model["aggregate"]["agreement"]
    assert agreement["count"] == 2 and -1 in agreement["members"]
    # but the human stays out of the panel's integrity tally (still the 5 panel citations)
    assert view_model["aggregate"]["integrity"]["total_citations"] == 5


def test_view_model_without_a_sidecar_offers_only_the_run_controls(tmp_path: Path):
    journal_path = tmp_path / "game.jsonl"
    _write_journal(path=journal_path)

    view_model = build_evaluation_view_model(journal_path=journal_path)

    assert view_model["has_evaluation"] is False
    assert view_model["members"] == []
    assert view_model["coverage"] is None
    assert len(view_model["spans"]) == 4  # the trace still renders so the page can show the game
