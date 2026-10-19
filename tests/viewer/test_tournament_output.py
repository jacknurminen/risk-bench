from risk_bench.viewer.tournament import _aggregate_output_volume


def test_output_pools_counts_and_exposure_across_unequal_and_unfinished_games():
    first = {
        "players": [{"name": name} for name in ("Alice", "Bob", "Silent")],
        "steps": [{"turn": turn} for turn in (0, 0, 1, 1, 2)],
        "messages": [
            {"sender": "Alice", "visibility": "public", "text": " hello\nthere "},
            {"sender": "Alice", "visibility": "private", "recipient": "Bob", "text": "one two three four"},
            {"sender": "Bob", "visibility": "public", "text": "   "},
        ],
        "thoughts": [{"player": "Alice", "text": "one two three"}, {"player": "Alice", "text": "four"}],
        "notes": [{"player": "Alice", "text": "Not counted"}],
        "result": {"winner": "Alice"},
    }
    second = {
        "players": list(reversed(first["players"])),
        "steps": [{"turn": turn} for turn in range(5)],
        "messages": [{"sender": "Alice", "visibility": "public",
                      "text": "one two three four five six seven eight nine"}],
        "thoughts": [],
        "result": None,
    }
    output = _aggregate_output_volume([("g1", first), ("g2", second)])
    assert output["game_count"] == 2
    assert output["unfinished_games"] == 1
    assert output["includes_setup"] is True
    alice, bob, silent = output["players"]
    assert alice == {
        "name": "Alice", "games": 2, "recorded_turns": 8,
        "messages": 3, "public_messages": 2, "private_messages": 1, "message_words": 15,
        "thoughts": 2, "thought_words": 4,
        "messages_per_game": 1.5, "messages_per_turn": 3 / 8, "words_per_message": 5,
        "thoughts_per_game": 1, "thoughts_per_turn": 2 / 8, "words_per_thought": 2,
    }
    assert bob["messages"] == 1  # receiving Alice's private message is not output
    assert bob["words_per_message"] == 0  # an empty entry differs from having no entries
    assert silent["messages_per_game"] == silent["messages_per_turn"] == 0
    assert silent["words_per_message"] is silent["words_per_thought"] is None


def test_output_handles_missing_logs_and_empty_timeline_without_inventing_exposure():
    output = _aggregate_output_volume([("g1", {"players": [{"name": "Alice"}], "steps": [], "result": None})])
    assert output["includes_setup"] is False
    row = output["players"][0]
    assert row["messages"] == row["thoughts"] == row["recorded_turns"] == 0
    assert row["messages_per_turn"] is row["thoughts_per_turn"] is row["words_per_message"] is None
    assert _aggregate_output_volume([])["players"] == []
