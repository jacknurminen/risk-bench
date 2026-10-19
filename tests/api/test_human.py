import time

from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
from risk_bench.runner.journal import JournalWriter, read_journal, restore_game
from tests.api.test_app import client_for


def wait_for(client, url, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        body = response.json()
        if predicate(body):
            return body
        time.sleep(0.03)
    raise AssertionError(f"Timed out waiting for {url}")


def launch_human(client, max_turns=3):
    response = client.post("/api/games", json={"players": ["human", "random", "random"], "max_turns": max_turns})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_human_game_plays_to_completion_and_records_actions_messages_and_reasoning(tmp_path):
    with client_for(tmp_path) as client:
        game_id = launch_human(client)
        url = f"/api/games/{game_id}/human"
        page = client.get(f"/games/{game_id}", follow_redirects=False)
        assert page.status_code == 200
        assert "<svg" in page.text and 'id="timeline"' in page.text and 'id="statsModal"' in page.text
        assert 'id="humanTurn"' in page.text and "Confirm move" in page.text
        assert f'"/api/games/{game_id}/human"' in page.text
        assert client.get(f"/games/{game_id}/play", follow_redirects=False).headers["location"] == f"/games/{game_id}"
        last_request = None
        saw_negotiation = False
        saw_attack = False
        noted_turns = []
        saved_notes = None
        for _ in range(100):
            body = wait_for(
                client, url, lambda b: b["finished"] or (b["request"] and b["request"]["request_id"] != last_request)
            )
            if body["finished"]:
                break
            request = body["request"]
            assert request["view"]["round_number"] == (
                0 if request["view"]["turn_number"] == 0 else (request["view"]["turn_number"] - 1) // 3 + 1
            )
            assert request["view"]["notes"] == saved_notes
            last_request = request["request_id"]
            reply = {"request_id": last_request, "thought": "Testing a human decision"}
            if request["kind"] == "decision":
                # Place armies, end attacks, and skip fortification so the game reaches the limit quickly.
                actions = request["actions"]
                placements = [(i, a) for i, a in enumerate(actions) if a["kind"] == "place_armies"]
                if len(placements) >= 2 and placements[0][1]["count"] > 1:
                    reply["placements"] = [
                        {"action_index": placements[0][0], "count": 1},
                        {"action_index": placements[1][0], "count": placements[0][1]["count"] - 1},
                    ]
                    assert client.post(url, json=reply).status_code == 202
                    continue
                saw_attack |= any(a["kind"] == "attack" for a in actions)
                reply["action_index"] = next(
                    (i for i, a in enumerate(actions) if a["kind"] in ("end_attack_phase", "skip_fortify")), 0
                )
            elif request["kind"] == "notes":
                assert request["finished_round"] == (request["finished_turn"] - 1) // 3 + 1
                noted_turns.append(request["finished_turn"])
                reply = {"request_id": last_request}
                if len(noted_turns) == 1:
                    saved_notes = "Watch the northern border."
                    reply["notes"] = saved_notes
                elif len(noted_turns) == 3:
                    saved_notes = ""
                    reply["notes"] = saved_notes
                # Omission skips the second noting pass and retains the notebook.
            else:
                saw_negotiation = True
            assert client.post(url, json=reply).status_code == 202
        else:
            raise AssertionError("Human game never finished")
        assert saw_negotiation and saw_attack
        assert noted_turns == [1, 2, 3]
        records = read_journal(tmp_path / "games" / game_id / "game.jsonl")
        assert records[-1]["type"] == "result"
        assert any(r["type"] == "thought" and r["player"] == "human" for r in records)
        assert any(r["type"] == "action" and r["player"] == "human" for r in records)
        notes = [r for r in records if r["type"] == "note" and r["player"] == "human"]
        assert [(r["turn"], r["text"]) for r in notes] == [(1, "Watch the northern border."), (3, "")]
        assert not any(r["type"] == "message" and "Watch the northern border" in r["message"]["text"] for r in records)
        assert client.post(url, json=reply).status_code == 409
        assert 'id="humanTurn"' not in client.get(f"/games/{game_id}").text


def test_pause_resume_invalidates_controls_and_turn_card_data_hides_opponents_secrets(tmp_path):
    with client_for(tmp_path) as client:
        game_id = launch_human(client)
        url = f"/api/games/{game_id}/human"
        body = wait_for(client, url, lambda b: b["request"])
        old_request = body["request"]["request_id"]
        assert client.get(f"/api/games/{game_id}").json()["running"] is True
        assert all(count == 1 for t, count in body["view"]["armies"].items() if body["view"]["owner"][t] != "human")
        assert client.post(f"/api/games/{game_id}/pause").status_code == 204
        path = tmp_path / "games" / game_id / "game.jsonl"
        game = restore_game(path)
        writer = JournalWriter(path, append=True)
        writer.write_message(ChatMessage(0, "random", MessageVisibility.PRIVATE, "random#2", "hidden pact"))
        writer.write_message(ChatMessage(0, "random", MessageVisibility.PRIVATE, "human", "visible pact"))
        writer.write_thought("random", "hidden reasoning")
        writer.close()
        paused = client.get(url).json()
        assert client.get(f"/api/games/{game_id}").json()["running"] is False
        assert paused["request"] is None and not paused["running"]
        assert [m["text"] for m in paused["view"]["chat"]] == ["visible pact"]
        assert "hidden reasoning" not in str(paused) and "hidden pact" not in str(paused)
        assert paused["view"]["mission"] == game.state.players["human"].mission.description
        assert all(
            set(p) == {"player_id", "territory_count", "army_count", "card_count", "income", "is_eliminated"}
            for p in paused["view"]["players"]
        )
        assert client.post(url, json={"request_id": old_request, "action_index": 0}).status_code == 409
        assert client.post(f"/api/games/{game_id}/resume").status_code == 204
        resumed = wait_for(client, url, lambda b: b["request"] and b["request"]["request_id"] != old_request)
        assert client.get(f"/api/games/{game_id}").json()["running"] is True
        assert client.post(url, json={"request_id": old_request, "action_index": 0}).status_code == 409
        current = resumed["request"]["request_id"]
        assert client.post(url, json={"request_id": current, "action_index": 0, "count": 9999}).status_code == 409
        assert client.post(url, json={"request_id": current, "action_index": 0, "count": 1.5}).status_code == 422
        assert (
            client.post(
                url, json={"request_id": current, "action_index": 0, "count": 2, "message": "Hello table"}
            ).status_code
            == 202
        )
        wait_for(client, url, lambda b: b["request"] and b["request"]["request_id"] != current)
        records = read_journal(path)
        assert any(r["type"] == "message" and r["message"]["text"] == "Hello table" for r in records)


def test_human_launch_rejects_multiple_seats_and_parallel_games(tmp_path):
    with client_for(tmp_path) as client:
        for players, parallel in [(["human", "human", "random"], 1), (["human", "random", "random"], 2)]:
            response = client.post("/api/games", json={"players": players, "parallel_games": parallel})
            assert response.status_code == 400


def test_agent_only_games_keep_the_original_reader_without_human_controls(tmp_path):
    from tests.api.test_app import client_with_finished_game

    client, game_id = client_with_finished_game(tmp_path)
    page = client.get(f"/games/{game_id}")
    assert page.status_code == 200
    assert 'id="timeline"' in page.text and 'id="statsModal"' in page.text
    assert 'id="humanTurn"' not in page.text and "/assets/reader/human.js" not in page.text
