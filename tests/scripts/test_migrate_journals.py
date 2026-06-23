from scripts.migrate_journals import _fold_records


def _battle_resolved(from_territory: str, to_territory: str, attacker_losses: int, defender_losses: int) -> dict:
    return {
        "kind": "battle_resolved",
        "attacker": "alice",
        "defender": "bob",
        "from_territory": from_territory,
        "to_territory": to_territory,
        "outcome": {
            "attacker_dice": [6, 6, 6],
            "defender_dice": [1],
            "attacker_losses": attacker_losses,
            "defender_losses": defender_losses,
        },
    }


def test_fold_collapses_a_legacy_attack_and_occupy_into_one_attack():
    # alaska reaches 4 armies, conquers northwest_territory losing none, then occupies with 2;
    # a thought sits between the battle events and the occupy, as a real journal records it
    legacy = [
        {"type": "header", "schema_version": 1, "seed": 0, "player_specs": {"alice": "random"}},
        {"type": "action", "player": "alice", "action": {"kind": "place_armies", "territory": "alaska", "count": 3}},
        {
            "type": "events",
            "events": [{"kind": "armies_placed", "player_id": "alice", "territory": "alaska", "count": 3}],
        },
        {
            "type": "action",
            "player": "alice",
            "action": {
                "kind": "attack",
                "from_territory": "alaska",
                "to_territory": "northwest_territory",
                "attacker_dice": 3,
            },
        },
        {
            "type": "events",
            "events": [
                _battle_resolved("alaska", "northwest_territory", attacker_losses=0, defender_losses=1),
                {
                    "kind": "territory_conquered",
                    "player_id": "alice",
                    "territory": "northwest_territory",
                    "from_territory": "alaska",
                },
            ],
        },
        {"type": "thought", "player": "alice", "text": "pressing the advantage"},
        {"type": "action", "player": "alice", "action": {"kind": "occupy", "armies_to_move": 2}},
    ]

    folded = _fold_records(records=legacy)

    attacks = [
        record["action"]
        for record in folded
        if record.get("type") == "action" and record["action"]["kind"] == "attack"
    ]
    assert len(attacks) == 1
    # alaska held 4 after a lossless roll; advancing 2 means leaving 2 behind
    assert attacks[0]["max_attacks"] == 1
    assert attacks[0]["armies_to_leave_behind"] == 2
    assert not any(record.get("type") == "action" and record["action"]["kind"] == "occupy" for record in folded)
    assert any(record.get("type") == "thought" for record in folded)  # observational records are preserved


def test_fold_defaults_a_non_conquering_attack_to_a_single_roll():
    legacy = [
        {"type": "header", "schema_version": 1, "seed": 0, "player_specs": {"alice": "random"}},
        {
            "type": "action",
            "player": "alice",
            "action": {
                "kind": "attack",
                "from_territory": "alaska",
                "to_territory": "northwest_territory",
                "attacker_dice": 2,
            },
        },
        {
            "type": "events",
            "events": [_battle_resolved("alaska", "northwest_territory", attacker_losses=1, defender_losses=0)],
        },
    ]

    [attack] = [record["action"] for record in _fold_records(records=legacy) if record["type"] == "action"]

    assert attack["max_attacks"] == 1
    assert attack["armies_to_leave_behind"] == 1


def test_fold_leaves_already_current_journals_untouched():
    current = [
        {"type": "header", "schema_version": 2, "seed": 0, "player_specs": {"alice": "random"}},
        {
            "type": "action",
            "player": "alice",
            "action": {
                "kind": "attack",
                "from_territory": "alaska",
                "to_territory": "northwest_territory",
                "attacker_dice": 3,
                "max_attacks": 4,
                "armies_to_leave_behind": 2,
            },
        },
    ]

    assert _fold_records(records=current) is current
