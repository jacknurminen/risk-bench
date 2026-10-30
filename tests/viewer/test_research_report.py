import copy
import json

from risk_bench.runner.codebook_v3 import Adjudication
from risk_bench.runner.evaluation_validation_v3 import validate_adjudication
from risk_bench.runner.evaluation_versions import version_for
from risk_bench.runner.journal import read_journal
from risk_bench.runner.trace_index import build_trace_index
from risk_bench.viewer.evaluation import build_evaluation_view_model
from risk_bench.viewer.research import game_context, research_report, tournament_research
from tests.v3_fixtures import PLAYERS, two_tag_adjudication, write_evaluation, write_journal


def evaluated_view(tmp_path):
    path = tmp_path / "game.jsonl"
    write_journal(path)
    units = validate_adjudication(
        Adjudication.model_validate(two_tag_adjudication()), build_trace_index(read_journal(path)), PLAYERS
    )
    sidecar = write_evaluation(path, units=units)
    lines = [json.loads(line) for line in sidecar.read_text().splitlines()]
    lines[0].update(schema_version=5, codebook_version="historical-two-tag")
    sidecar.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return build_evaluation_view_model(path, version_for(path, None))


def summarize(view, specs=None):
    return research_report(view, specs or {"alice": "random", "bob": "human", "carla": "random"})


def test_counts_actor_obligations_and_keeps_unestablished_and_invalid_outcomes_separate(tmp_path):
    view = evaluated_view(tmp_path)
    base = view["members"][0]["commitments"][0]
    view["members"][0]["commitments"] += [
        dict(base, id="second", agreement_id="pact", status="fulfilled"),
        dict(base, id="offer", established=False, status="closed", reason="Rejected"),
        dict(base, id="uncertain", established=False, status="unresolved", reason="Unclear acceptance"),
        dict(base, id="broken", valid=False, needs_review=True, issues=["Broken citation"]),
        dict(base, id="human", actor="bob", status="fulfilled", action_provenance="human"),
    ]
    result = summarize(view)
    alice, bob, _ = result["members"][0]["players"]
    assert alice["established"] == 2
    assert alice["unestablished"] == 2
    assert alice["outcomes"] == {"violated": 1, "fulfilled": 1}
    assert alice["unestablished_statuses"] == {"closed": 1, "unresolved": 1}
    assert alice["excluded"] == 1
    assert alice["adherence"] == {"fulfilled": 1, "resolved": 2}
    assert bob["kind"] == "human" and bob["adherence"]["resolved"] == 0
    assert result["scope"] == "historical_statements"


def test_deception_counts_proposition_once_across_bases_but_new_messages_are_new_incidents(tmp_path):
    view = evaluated_view(tmp_path)
    base = view["members"][0]["statements"][0]
    view["members"][0]["statements"] += [
        dict(base, id="second-basis", basis="recorded_fact"),
        dict(base, id="new-message", passage={**base["passage"], "span": 2}),
        dict(base, id="contradiction-only", status="contradicted"),
        dict(base, id="excluded", valid=False),
    ]
    alice = summarize(view)["members"][0]["players"][0]
    assert alice["deception_incidents"] == 2
    assert alice["deception_records"][0]["ids"] == ["s1", "second-basis"]
    assert alice["excluded"] == 1


def test_role_counts_use_earliest_documented_proposal_and_do_not_invent_silent_participants(tmp_path):
    view = evaluated_view(tmp_path)
    base = view["members"][0]["commitments"][0]
    proposed = {"status": "proposed", "cutoff_span": 1}
    agreed = {"status": "agreed", "cutoff_span": 2}
    view["members"][0]["commitments"] = [
        dict(base, id="a1", agreement_id="pact", status_history=[proposed, agreed]),
        dict(base, id="a2", agreement_id="pact", status_history=[proposed, agreed]),
        dict(base, id="b1", actor="bob", agreement_id="pact", status_history=[agreed]),
        dict(base, id="unknown", actor="carla", agreement_id="other", status_history=[agreed]),
    ]
    alice, bob, carla = summarize(view)["members"][0]["players"]
    assert alice["roles"] == {"initiator": 1, "accepting_participant": 0, "unknown": 0}
    assert bob["roles"]["accepting_participant"] == 1
    assert carla["roles"] == {"initiator": 0, "accepting_participant": 0, "unknown": 1}


def test_screening_distinguishes_unclear_missing_and_outside_cutoff(tmp_path):
    view = evaluated_view(tmp_path)
    view["members"][0]["coverage"][0]["status"] = "unclear"
    view["members"][0]["coverage"][1]["status"] = "missing"
    view["spans"].append({"span": 99, "layer": "message", "player": "alice"})
    alice, bob, _ = summarize(view)["members"][0]["players"]
    assert alice["coverage"] == {"unclear": 1, "outside_cutoff": 1}
    assert bob["coverage"] == {"missing": 1}
    assert alice["messages"] == 2
    view["coverage"]["source_matches"] = False
    assert summarize(view)["members"][0]["players"][0]["coverage"] == {"source_mismatch": 2}


def test_tournament_keeps_instruments_and_evaluators_separate_and_counts_games_once(tmp_path):
    view = evaluated_view(tmp_path)
    report = summarize(view)
    second = copy.deepcopy(report)
    second["members"].append({**copy.deepcopy(second["members"][0]), "index": 1, "spec": "another:coder"})
    third = copy.deepcopy(report)
    third["codebook_hash"] = "different-instrument"
    context = {"players": [{"name": name, "turns_survived": 3} for name in ("alice", "bob", "carla")]}
    result = tournament_research(
        [
            ("g1", context, report),
            ("g2", context, second),
            ("g3", context, third),
            ("g4", context, {"has_evaluation": False}),
        ]
    )
    assert len(result["cohorts"]) == 3
    rows = result["cohorts"][0]["players"]
    assert [row["name"] for row in rows] == ["alice", "bob", "carla"]  # two random seats stay apart
    alice = rows[0]
    assert alice["model"] == "random" and alice["experiment_profile"] is None
    assert alice["turns_survived"] == 6 and alice["player_games"] == 2
    assert alice["observed_games"] == ["g1", "g2"]
    pooled = [row for row in rows if row["model"] == "random"]
    assert sum(row["established"] for row in pooled) == 2
    assert sum(row["deception_incidents"] for row in pooled) == 2
    assert result["availability"][-1]["status"] == "Not evaluated"
    json.dumps(result)


def test_survival_uses_game_turns_not_action_count_and_includes_elimination_turn():
    model = {
        "players": [{"name": "a", "mission": "Mission A"}, {"name": "b", "mission": "Mission B"}],
        "messages": [{"sender": "a"}, {"sender": "b"}, {"sender": "a"}],
        "steps": [
            {"turn": t, "round": r, "players": [{"eliminated": gone}, {"eliminated": False}]}
            for t, r, gone in [(0, 0, False), (1, 1, False), (1, 1, False), (2, 1, True), (3, 2, True)]
        ],
        "ancestry": {"parent": "original"},
    }
    context = game_context(model, {"a": "random", "b": "human"}, "Mission completed")
    a, b = context["players"]
    assert a["turns_survived"] == 2 and a["eliminated_turn"] == 2 and a["messages"] == 2
    assert b["turns_survived"] == 3 and b["eliminated_turn"] is None
    assert context["rounds_played"] == 2 and context["related_branch"]


def test_tournament_keeps_methods_separate_even_for_the_same_model_and_game(tmp_path):
    report = summarize(evaluated_view(tmp_path))
    context = {"players": [{"name": name, "turns_survived": 1} for name in ("alice", "bob", "carla")]}
    result = tournament_research([
        ("g1", context, {**report, "method": "live_rounds"}),
        ("g1", context, {**report, "method": "whole_game"}),
    ])
    assert {cohort["method"] for cohort in result["cohorts"]} == {"live_rounds", "whole_game"}
    assert len(result["cohorts"]) == 2
    for cohort in result["cohorts"]:
        assert cohort["players"][0]["established"] == 1


def test_tournament_rows_name_the_experiment_profile(tmp_path):
    report = summarize(evaluated_view(tmp_path))
    context = {
        "players": [
            {"name": "alice", "turns_survived": 1, "experiment_profile": {"id": "deceiver", "label": "Deceiver"}},
            {"name": "bob", "turns_survived": 1},
            {"name": "carla", "turns_survived": 1},
        ]
    }
    [cohort] = tournament_research([("g1", context, report)])["cohorts"]
    profiles = {row["name"]: row["experiment_profile"] for row in cohort["players"]}
    assert profiles == {"alice": "Deceiver", "bob": None, "carla": None}
