import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from risk_bench.api.app import create_app
from risk_bench.api.processes import GameProcessManager
from risk_bench.runner.evaluation import Evaluation, EvaluatorTag
from risk_bench.runner.evaluation_store import EvaluationWriter, evaluation_path_for_journal
from tests.runner.test_journal import PLAYER_NAMES, write_game_journal


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(games_directory=tmp_path / "games"))


def client_with_finished_game(tmp_path: Path) -> tuple[TestClient, str]:
    game_directory = tmp_path / "games" / "first-game"
    game_directory.mkdir(parents=True)
    write_game_journal(path=game_directory / "game.jsonl", seed=11, max_turns=2_000)
    return client_for(tmp_path=tmp_path), "first-game"


def test_an_empty_games_directory_lists_no_games(tmp_path: Path):
    response = client_for(tmp_path=tmp_path).get("/api/games")

    assert response.status_code == 200
    assert response.json() == {"games": [], "tournaments": []}


def test_finished_games_are_listed_with_their_outcome(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    [game] = client.get("/api/games").json()["games"]

    assert game["id"] == game_id
    assert game["players"] == PLAYER_NAMES
    assert game["finished"] is True
    assert game["winner"] in PLAYER_NAMES
    assert game["turn_count"] >= 1
    assert game["has_commentary"] is False
    assert game["input_tokens"] is None  # no trace: random players never called an API


def test_an_unfinished_game_lists_the_turn_it_reached(tmp_path: Path):
    unfinished_journal(games_directory=tmp_path / "games", name="paused")

    [game] = client_for(tmp_path=tmp_path).get("/api/games").json()["games"]

    assert game["finished"] is False
    assert game["turn_count"] >= 1  # the latest turn_started seen in the journal


def test_the_game_view_model_is_served_as_json(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    model = client.get(f"/api/games/{game_id}").json()

    assert len(model["steps"]) > 1
    assert model["result"]["winner"] in PLAYER_NAMES


def test_the_game_page_renders_the_viewer(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    page = client.get(f"/games/{game_id}")

    assert page.status_code == 200
    assert "<svg" in page.text
    assert "live: false" in page.text  # the game is finished: no polling


def test_an_unfinished_game_page_polls_the_api(tmp_path: Path):
    game_directory = tmp_path / "games" / "running"
    game_directory.mkdir(parents=True)
    path = game_directory / "game.jsonl"
    write_game_journal(path=path, seed=11, max_turns=2_000)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # drop the result line

    page = client_for(tmp_path=tmp_path).get("/games/running")

    assert "live: true" in page.text
    assert '"/api/games/running"' in page.text


def test_a_corrupted_journal_yields_a_conflict_not_a_crash(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)
    path = tmp_path / "games" / game_id / "game.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    # duplicating an action makes the journal stop replaying cleanly
    path.write_text("\n".join(lines[:30] + lines[20:]) + "\n", encoding="utf-8")

    for url in (f"/games/{game_id}", f"/api/games/{game_id}"):
        response = client.get(url)
        assert response.status_code == 409, url
        assert "replay" in response.json()["detail"]


def test_unknown_and_malformed_game_ids_are_rejected(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)

    assert client.get("/api/games/no-such-game").status_code == 404
    assert client.get("/api/games/..%2Fescape").status_code == 404


def test_the_index_page_is_served_at_the_root(tmp_path: Path):
    response = client_for(tmp_path=tmp_path).get("/")

    assert response.status_code == 200
    assert "risk-bench" in response.text


def test_the_model_catalog_offers_tiers_and_the_baseline(tmp_path: Path):
    catalog = client_for(tmp_path=tmp_path).get("/api/models").json()

    assert catalog["baseline"] == "random"
    assert catalog["tiers_by_provider"]["anthropic"]["lo"] == "claude-haiku-4-5"
    assert set(catalog["tiers_by_provider"]) == {"anthropic", "google", "openai"}
    assert catalog["efforts"] == ["low", "medium", "high"]


def test_launch_requests_are_validated(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)

    too_few = client.post("/api/games", json={"players": ["random", "random"]})
    assert too_few.status_code == 400
    assert "3-6" in too_few.json()["detail"]

    bad_provider = client.post("/api/games", json={"players": ["random", "random", "frontier:hi"]})
    assert bad_provider.status_code == 400
    assert "Unknown provider" in bad_provider.json()["detail"]


def unfinished_journal(games_directory: Path, name: str) -> Path:
    (games_directory / name).mkdir(parents=True, exist_ok=True)
    path = games_directory / name / "game.jsonl"
    write_game_journal(path=path, seed=11, max_turns=2_000)
    # cut the journal mid-game, as if the game had been paused halfway
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[: len(lines) // 2]) + "\n", encoding="utf-8")
    return path


def test_pausing_a_running_game_terminates_its_worker(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)
    # six random players and an unreachable turn limit keep the game running long enough to pause
    response = client.post("/api/games", json={"players": ["random"] * 6, "seed": 5, "max_turns": 1_000_000})
    assert response.status_code == 201
    game_id = response.json()["id"]

    [game] = client.get("/api/games").json()["games"]
    assert game["running"] is True and game["finished"] is False

    assert client.post(f"/api/games/{game_id}/pause").status_code == 204

    [game] = client.get("/api/games").json()["games"]
    assert game["running"] is False and game["finished"] is False  # paused, resumable


def test_pausing_a_game_that_is_not_running_is_rejected(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    response = client.post(f"/api/games/{game_id}/pause")

    assert response.status_code == 409
    assert "not running" in response.json()["detail"]


def test_resuming_a_paused_game_plays_it_to_completion(tmp_path: Path):
    unfinished_journal(games_directory=tmp_path / "games", name="paused")
    client = client_for(tmp_path=tmp_path)

    assert client.post("/api/games/paused/resume").status_code == 204

    deadline = time.monotonic() + 90
    finished = False
    while time.monotonic() < deadline and not finished:
        time.sleep(0.5)
        [game] = client.get("/api/games").json()["games"]
        finished = game["finished"]
    assert finished, "the resumed game never finished"
    assert game["winner"] in PLAYER_NAMES


def test_resuming_a_finished_game_is_rejected(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    response = client.post(f"/api/games/{game_id}/resume")

    assert response.status_code == 409
    assert "finished" in response.json()["detail"]


def test_deleting_a_game_removes_its_folder_and_listing(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    assert client.delete(f"/api/games/{game_id}").status_code == 204

    assert not (tmp_path / "games" / game_id).exists()
    assert client.get("/api/games").json()["games"] == []
    assert client.get(f"/api/games/{game_id}").status_code == 404


def test_deleting_a_running_game_is_rejected(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)
    # six random players and an unreachable turn limit keep the game running long enough to hit delete
    game_id = client.post("/api/games", json={"players": ["random"] * 6, "seed": 5, "max_turns": 1_000_000}).json()[
        "id"
    ]

    response = client.delete(f"/api/games/{game_id}")

    assert response.status_code == 409
    assert "running" in response.json()["detail"]
    # once paused, the same delete goes through
    assert client.post(f"/api/games/{game_id}/pause").status_code == 204
    assert client.delete(f"/api/games/{game_id}").status_code == 204
    assert client.get("/api/games").json()["games"] == []


def test_deleting_an_unknown_game_is_a_404(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)

    assert client.delete("/api/games/no-such-game").status_code == 404


def test_trace_endpoints_serve_summaries_and_full_records(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)
    trace_record = {
        "seq": 1,
        "time": "2026-06-12T10:00:00+00:00",
        "turn": 2,
        "kind": "decision",
        "caller": PLAYER_NAMES[0],
        "model": "claude-sonnet-5",
        "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "a very long prompt"}],
        "tools": [{"name": "attack", "description": "Attack.", "parameters": {"type": "object"}}],
        "response": {"text": None, "tool_calls": [{"id": "c1", "name": "attack", "arguments": {}}]},
        "error": None,
        "input_tokens": 2_341,
        "output_tokens": 89,
        "latency_ms": 1_200,
    }
    trace_path = tmp_path / "games" / game_id / "game.trace.jsonl"
    trace_path.write_text(json.dumps(trace_record) + "\n", encoding="utf-8")

    [summary] = client.get(f"/api/games/{game_id}/trace").json()["calls"]
    assert summary["seq"] == 1
    assert summary["caller"] == PLAYER_NAMES[0]
    assert summary["tool_call_names"] == ["attack"]
    assert summary["message_count"] == 2
    assert "messages" not in summary  # summaries stay light; bodies come from the detail endpoint

    detail = client.get(f"/api/games/{game_id}/trace/1").json()
    assert detail["messages"][1]["content"] == "a very long prompt"
    assert detail["tools"][0]["name"] == "attack"

    assert client.get(f"/api/games/{game_id}/trace/99").status_code == 404

    # the game listing rolls the trace up into token and cost totals
    [game] = client.get("/api/games").json()["games"]
    assert (game["input_tokens"], game["output_tokens"]) == (2_341, 89)
    assert game["cost_usd"] == (2_341 * 2.00 + 89 * 10.00) / 1_000_000


def test_a_game_without_a_trace_lists_no_calls(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    assert client.get(f"/api/games/{game_id}/trace").json() == {"calls": []}


def test_a_parallel_launch_becomes_a_grouped_tournament(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)

    response = client.post(
        "/api/games",
        json={"players": ["random", "random", "random"], "seed": 7, "max_turns": 40, "parallel_games": 3},
    )
    assert response.status_code == 201
    assert response.json()["tournament"] is True
    tournament_id = response.json()["id"]
    assert (tmp_path / "games" / tournament_id / "tournament.json").is_file()

    payload = client.get("/api/games").json()
    [tournament] = payload["tournaments"]
    assert tournament["id"] == tournament_id
    assert tournament["game_ids"] == [f"{tournament_id}-g{index}" for index in (1, 2, 3)]
    assert len(payload["games"]) == 3
    assert all(game["tournament_id"] == tournament_id for game in payload["games"])
    assert sorted(game["seed"] for game in payload["games"]) == [7, 8, 9]  # consecutive seeds

    deadline = time.monotonic() + 90
    finished = 0
    while time.monotonic() < deadline and finished < 3:
        time.sleep(0.5)
        finished = sum(game["finished"] for game in client.get("/api/games").json()["games"])
    assert finished == 3, "the tournament's games never all finished"


def test_oversized_tournaments_are_rejected(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)

    response = client.post("/api/games", json={"players": ["random"] * 3, "parallel_games": 21})

    assert response.status_code == 422  # pydantic bounds the field


def test_the_tournament_stats_page_and_view_model_are_served(tmp_path: Path):
    from tests.viewer.test_tournament import tournament_directory_with_games

    tournament_directory = tournament_directory_with_games(tmp_path=tmp_path / "games", seeds=[11, 12])
    client = client_for(tmp_path=tmp_path)
    tournament_id = tournament_directory.name

    page = client.get(f"/tournaments/{tournament_id}")
    assert page.status_code == 200
    assert "cross-game statistics" in page.text
    assert "const LIVE = false" in page.text  # both members finished: no polling

    model = client.get(f"/api/tournaments/{tournament_id}").json()
    assert len(model["games"]) == 2
    assert set(model["metrics"]) >= {"army_share", "territories"}

    assert client.get("/tournaments/no-such-tournament").status_code == 404


def test_launching_a_game_spawns_a_process_that_plays_it_to_completion(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)

    response = client.post("/api/games", json={"players": ["random", "random", "random"], "seed": 3, "max_turns": 40})
    assert response.status_code == 201
    assert response.json()["tournament"] is False
    game_id = response.json()["id"]

    deadline = time.monotonic() + 90
    result = None
    while time.monotonic() < deadline and result is None:
        time.sleep(0.5)
        result = client.get(f"/api/games/{game_id}").json()["result"]
    assert result is not None, "the launched game never finished"
    assert result["turn_count"] >= 1

    [game] = client.get("/api/games").json()["games"]
    assert game["id"] == game_id and game["finished"] is True


def client_with_evaluated_game(tmp_path: Path) -> tuple[TestClient, str]:
    client, game_id = client_with_finished_game(tmp_path=tmp_path)
    evaluation = Evaluation(
        evaluator_spec="anthropic:hi",
        tags=(
            EvaluatorTag(
                player=PLAYER_NAMES[0],
                behavior="deception",
                turn=1,
                evidence=(),
                confidence=2,
                note="a panel tag to verify",
            ),
        ),
        syntheses=(),
    )
    journal_path = tmp_path / "games" / game_id / "game.jsonl"
    writer = EvaluationWriter(path=evaluation_path_for_journal(journal_path=journal_path))
    writer.write_header(panel=["anthropic:hi"], record_count=1, span_count=1)
    writer.write_evaluation(evaluator_index=0, evaluation=evaluation)
    writer.close()
    return client, game_id


def test_verifying_a_tag_round_trips_through_the_api(tmp_path: Path):
    client, game_id = client_with_evaluated_game(tmp_path=tmp_path)
    tag_key = {"evaluator_index": 0, "player": PLAYER_NAMES[0], "behavior": "deception", "turn": 1}

    set_response = client.put(f"/api/games/{game_id}/verifications", json={**tag_key, "verified": True})
    assert set_response.status_code == 204
    (tag,) = client.get(f"/api/games/{game_id}/evaluation").json()["members"][0]["tags"]
    assert tag["verified"] is True

    # verifying again is idempotent, and clearing brings the tag back to unverified
    assert client.put(f"/api/games/{game_id}/verifications", json={**tag_key, "verified": True}).status_code == 204
    clear = client.put(f"/api/games/{game_id}/verifications", json={**tag_key, "verified": False})
    assert clear.status_code == 204
    (tag,) = client.get(f"/api/games/{game_id}/evaluation").json()["members"][0]["tags"]
    assert tag["verified"] is False

    # clearing an already-clear tag stays a no-op, not an error
    assert client.put(f"/api/games/{game_id}/verifications", json={**tag_key, "verified": False}).status_code == 204


def test_verifying_a_tag_the_evaluation_does_not_hold_is_not_found(tmp_path: Path):
    client, game_id = client_with_evaluated_game(tmp_path=tmp_path)

    wrong_turn = {"evaluator_index": 0, "player": PLAYER_NAMES[0], "behavior": "deception", "turn": 99}
    response = client.put(f"/api/games/{game_id}/verifications", json={**wrong_turn, "verified": True})

    assert response.status_code == 404


def _write_raw_eval(path: Path, run_id: str, panel: list[str], behavior: str) -> None:
    header = {
        "type": "eval_header",
        "schema_version": 2,
        "run_id": run_id,
        "panel": panel,
        "coverage": {"record_count": 1, "span_count": 1},
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    tag = {
        "type": "eval_tag",
        "evaluator_index": 0,
        "evaluator": panel[0],
        "player": PLAYER_NAMES[0],
        "behavior": behavior,
        "turn": 1,
        "evidence": [],
        "confidence": 2,
        "note": "",
    }
    path.write_text(json.dumps(header) + "\n" + json.dumps(tag) + "\n", encoding="utf-8")


def client_with_two_evaluation_versions(tmp_path: Path) -> tuple[TestClient, str]:
    client, game_id = client_with_finished_game(tmp_path=tmp_path)
    game_directory = tmp_path / "games" / game_id
    # the current (newest) run and one archived run, each its own panel + tag
    _write_raw_eval(
        path=game_directory / "game.eval.jsonl", run_id="20260102-000000", panel=["openai:hi"], behavior="deception"
    )
    _write_raw_eval(
        path=game_directory / "game.eval.20260101-000000.jsonl",
        run_id="20260101-000000",
        panel=["anthropic:hi"],
        behavior="betrayal",
    )
    return client, game_id


def test_the_evaluation_view_model_lists_versions_and_defaults_to_current(tmp_path: Path):
    client, game_id = client_with_two_evaluation_versions(tmp_path=tmp_path)

    current = client.get(f"/api/games/{game_id}/evaluation").json()
    assert [version["run_id"] for version in current["versions"]] == ["20260102-000000", "20260101-000000"]
    assert current["version"] == "20260102-000000" and current["versions"][0]["is_current"] is True
    (current_tag,) = current["members"][0]["tags"]
    assert current_tag["behavior"] == "deception"


def test_an_older_version_can_be_selected_by_run_id(tmp_path: Path):
    client, game_id = client_with_two_evaluation_versions(tmp_path=tmp_path)

    archived = client.get(f"/api/games/{game_id}/evaluation?version=20260101-000000").json()
    assert archived["version"] == "20260101-000000"
    (archived_tag,) = archived["members"][0]["tags"]
    assert archived_tag["behavior"] == "betrayal"


def test_verifications_are_kept_per_version(tmp_path: Path):
    client, game_id = client_with_two_evaluation_versions(tmp_path=tmp_path)
    archived_key = {"evaluator_index": 0, "player": PLAYER_NAMES[0], "behavior": "betrayal", "turn": 1}

    # verify a tag that only the archived version holds, scoped to that version
    set_response = client.put(
        f"/api/games/{game_id}/verifications", json={**archived_key, "verified": True, "version": "20260101-000000"}
    )
    assert set_response.status_code == 204

    # it shows verified only when viewing that version; the current run is untouched
    (archived_tag,) = client.get(f"/api/games/{game_id}/evaluation?version=20260101-000000").json()["members"][0][
        "tags"
    ]
    assert archived_tag["verified"] is True
    (current_tag,) = client.get(f"/api/games/{game_id}/evaluation").json()["members"][0]["tags"]
    assert current_tag["verified"] is False

    # the archived tag does not exist in the current run, so verifying it there is a 404
    conflict = client.put(f"/api/games/{game_id}/verifications", json={**archived_key, "verified": True})
    assert conflict.status_code == 404


def _write_evaluator_trace(path: Path, callers: list[str]) -> None:
    records = [
        {
            "seq": sequence,
            "time": "2026-06-12T10:00:00+00:00",
            "turn": 5,
            "kind": "evaluation",
            "caller": caller,
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "tag this turn"}],
            "tools": [],
            "response": {"text": "ok", "tool_calls": []},
            "error": None,
            "input_tokens": 1_000,
            "output_tokens": 100,
            "latency_ms": 500,
        }
        for sequence, caller in enumerate(callers, start=1)
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


_CALL_COST_USD = (1_000 * 2.00 + 100 * 10.00) / 1_000_000  # one traced call at sonnet list prices


def test_a_running_evaluation_reports_live_progress_from_its_trace(tmp_path: Path, monkeypatch):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)
    _write_evaluator_trace(
        path=tmp_path / "games" / game_id / "game.eval.trace.jsonl",
        callers=["evaluator[0]", "evaluator[0]", "evaluator[1]"],
    )
    monkeypatch.setattr(GameProcessManager, "is_evaluating", lambda self, game_id: True)
    monkeypatch.setattr(GameProcessManager, "evaluating_panel", lambda self, game_id: ["anthropic:hi", "openai:hi"])

    payload = client.get(f"/api/games/{game_id}/evaluation").json()

    assert payload["running"] is True
    progress = payload["progress"]
    assert progress["state"] == "running"
    assert progress["completed_calls"] == 3 and progress["failed_calls"] == 0
    assert progress["cost_usd"] == round(3 * _CALL_COST_USD, 4)
    # each member is expected to make one call per turn plus the synthesis call
    assert [(member["calls"], member["spec"]) for member in progress["members"]] == [
        (2, "anthropic:hi"),
        (1, "openai:hi"),
    ]
    assert progress["expected_calls"] == 2 * progress["members"][0]["expected_calls"]


def test_a_trace_without_its_sidecar_is_reported_as_an_interrupted_run(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)
    # a flat eval trace with no game.eval.jsonl: the worker died mid-run (e.g. a server shutdown)
    _write_evaluator_trace(
        path=tmp_path / "games" / game_id / "game.eval.trace.jsonl",
        callers=["evaluator[0]", "evaluator[1]", "evaluator[2]"],
    )

    progress = client.get(f"/api/games/{game_id}/evaluation").json()["progress"]

    assert progress["state"] == "interrupted"
    assert progress["completed_calls"] == 3
    assert progress["cost_usd"] == round(3 * _CALL_COST_USD, 4)
    assert progress["last_call_at"]


def test_a_completed_run_reports_no_progress(tmp_path: Path):
    client, game_id = client_with_evaluated_game(tmp_path=tmp_path)
    _write_evaluator_trace(path=tmp_path / "games" / game_id / "game.eval.trace.jsonl", callers=["evaluator[0]"])

    payload = client.get(f"/api/games/{game_id}/evaluation").json()

    assert payload["running"] is False and payload["progress"] is None


def test_evaluation_usage_reports_per_member_tokens_and_cost(tmp_path: Path):
    client, game_id = client_with_evaluated_game(tmp_path=tmp_path)
    _write_evaluator_trace(
        path=tmp_path / "games" / game_id / "game.eval.trace.jsonl",
        callers=["evaluator[0]", "evaluator[1]", "evaluator[0]"],
    )

    usage = client.get(f"/api/games/{game_id}/evaluation/usage").json()

    assert [(member["index"], member["calls"]) for member in usage["members"]] == [(0, 2), (1, 1)]
    assert usage["members"][0]["input_tokens"] == 2_000
    assert usage["total"]["calls"] == 3 and usage["total"]["output_tokens"] == 300
    assert usage["total"]["cost_usd"] == round(3 * _CALL_COST_USD, 4)


def test_evaluation_usage_of_an_unevaluated_game_is_not_found(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    assert client.get(f"/api/games/{game_id}/evaluation/usage").status_code == 404


def test_the_trace_inspector_serves_the_evaluator_panel_source_per_version(tmp_path: Path):
    client, game_id = client_with_two_evaluation_versions(tmp_path=tmp_path)
    game_directory = tmp_path / "games" / game_id
    _write_evaluator_trace(path=game_directory / "game.eval.trace.jsonl", callers=["evaluator[0]"])
    _write_evaluator_trace(
        path=game_directory / "game.eval.20260101-000000.trace.jsonl", callers=["evaluator[0]", "evaluator[0]"]
    )

    current = client.get(f"/api/games/{game_id}/trace?source=evaluation").json()["calls"]
    assert len(current) == 1 and current[0]["caller"] == "evaluator[0]"
    archived = client.get(f"/api/games/{game_id}/trace?source=evaluation&version=20260101-000000").json()["calls"]
    assert len(archived) == 2
    detail = client.get(f"/api/games/{game_id}/trace/1?source=evaluation")
    assert detail.status_code == 200 and detail.json()["messages"][0]["content"] == "tag this turn"
    # the default source stays the game's own trace, which this game does not have
    assert client.get(f"/api/games/{game_id}/trace").json() == {"calls": []}


def test_the_analysis_artifact_is_computed_on_demand_per_version(tmp_path: Path):
    client, game_id = client_with_two_evaluation_versions(tmp_path=tmp_path)
    game_directory = tmp_path / "games" / game_id

    current = client.get(f"/api/games/{game_id}/analysis")
    assert current.status_code == 200
    assert current.json()["source"]["panel"] == ["openai:hi"]
    assert (game_directory / "game.analysis.json").is_file()  # persisted, so the next request just reads it

    archived = client.get(f"/api/games/{game_id}/analysis?version=20260101-000000")
    assert archived.json()["source"]["panel"] == ["anthropic:hi"]
    assert (game_directory / "game.analysis.20260101-000000.json").is_file()


def test_the_analysis_of_an_unevaluated_game_is_not_found(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    assert client.get(f"/api/games/{game_id}/analysis").status_code == 404
