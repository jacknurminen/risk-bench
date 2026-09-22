"""One-time migration: rewrite pre-fold game journals to the current schema in place.

Pre-fold journals (schema v1) recorded a single-roll ``attack`` followed by a
separate ``occupy`` action. The current engine folds the occupy into the
conquering attack and has no OCCUPY phase, so the current loaders reject those
journals. This script translates them on disk to schema v2 so they load
natively. Schema v3 only records that a game was played under the current
ruleset; a folded game keeps v2 so the loaders replay it under the legacy rules
it was actually played with.

The rewrite is faithful: each attack's army advance is derived from the recorded
battle losses and occupy amount, reproducing the original board exactly. Writes
are atomic (temp file + replace), so an interrupted run never leaves a
half-written journal.

Usage (run from the repository root):
    uv run python -m scripts.migrate_journals [--games-dir data/v2/games] [--dry-run]
"""

import argparse
import json
from pathlib import Path
from typing import Any, Final

from risk_bench.runner.journal import read_journal

# the schema the fold produces; v3 only changes the ruleset a game is judged by, which no rewrite can supply
_FOLDED_SCHEMA_VERSION: Final[int] = 2


def main() -> None:
    """Parse arguments and migrate every journal under the games directory."""
    parser = argparse.ArgumentParser(description="Rewrite pre-fold game journals to the current schema.")
    parser.add_argument(
        "--games-dir", type=Path, default=Path("data/v2/games"), help="Directory holding game journals."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    arguments = parser.parse_args()
    _migrate_directory(games_directory=arguments.games_dir, dry_run=arguments.dry_run)


def _migrate_directory(games_directory: Path, dry_run: bool) -> None:
    journals = sorted(games_directory.glob("**/game.jsonl"))
    migrated = sum(1 for path in journals if _migrate_journal(path=path, dry_run=dry_run))
    verb = "would migrate" if dry_run else "migrated"
    print(f"{verb} {migrated} of {len(journals)} journals in {games_directory} (the rest were already current).")


def _migrate_journal(path: Path, dry_run: bool) -> bool:
    records = read_journal(path=path)
    if not records or records[0].get("type") != "header":
        print(f"  skipped (not a journal): {path}")
        return False

    folded = _fold_records(records=records)
    already_current = folded is records and int(records[0].get("schema_version", 1)) >= _FOLDED_SCHEMA_VERSION
    if already_current:
        return False

    upgraded = [{**folded[0], "schema_version": _FOLDED_SCHEMA_VERSION}, *folded[1:]]
    if not dry_run:
        _write_journal(path=path, records=upgraded)
    print(f"  {'would migrate' if dry_run else 'migrated'}: {path}")
    return True


def _write_journal(path: Path, records: list[dict[str, Any]]) -> None:
    temporary = path.parent / f"{path.name}.tmp"
    temporary.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    temporary.replace(path)  # atomic swap so a crash never leaves a half-written journal


def _fold_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold legacy attack+occupy action pairs into the current single attack action.

    Each legacy attack becomes an attack carrying ``max_attacks`` and the
    ``armies_to_leave_behind`` that reproduces the recorded advance exactly, and
    the paired occupy record is dropped. The advance is derived from the recorded
    battle losses and occupy amount (tracked through the journal's events), so
    the replayed board matches the original and no later action is rejected.

    Args:
        records: The journal records as read by ``read_journal``.

    Returns:
        Records in the current schema; the same list object when no folding is needed.
    """
    if not _has_legacy_actions(records=records):
        return records

    folded_records: list[dict[str, Any]] = [records[0]]
    armies: dict[str, int] = {}
    last_conquest: tuple[str, str] | None = None
    occurrences = records[1:]
    for index, record in enumerate(occurrences):
        if record["type"] == "events":
            _track_event_armies(armies=armies, events=record["events"])
            folded_records.append(record)
        elif _is_action(record=record, kind="attack"):
            attack = record["action"]
            losses, conquered = _attack_outcome(events=_following_events(occurrences=occurrences, index=index))
            armies_advanced = _following_occupy_amount(occurrences=occurrences, index=index) if conquered else None
            armies_after_roll = armies.get(attack["from_territory"], 1) - losses
            leave_behind = max(1, armies_after_roll - armies_advanced) if armies_advanced is not None else 1
            last_conquest = (attack["from_territory"], attack["to_territory"]) if conquered else None
            folded = {**attack, "max_attacks": 1, "armies_to_leave_behind": leave_behind}
            folded_records.append({"type": "action", "player": record["player"], "action": folded})
        elif _is_action(record=record, kind="occupy"):
            if last_conquest is not None:
                source, target = last_conquest
                advanced = int(record["action"]["armies_to_move"])
                armies[source] = armies.get(source, 1) - advanced
                armies[target] = armies.get(target, 1) + advanced
                last_conquest = None
            # the advance now lives inside the attack; drop the standalone occupy record
        else:
            folded_records.append(record)
    return folded_records


def _has_legacy_actions(records: list[dict[str, Any]]) -> bool:
    return any(
        _is_action(record=record, kind="occupy")
        or (_is_action(record=record, kind="attack") and "max_attacks" not in record["action"])
        for record in records
    )


def _is_action(record: dict[str, Any], kind: str) -> bool:
    return record.get("type") == "action" and record.get("action", {}).get("kind") == kind


def _track_event_armies(armies: dict[str, int], events: list[dict[str, Any]]) -> None:
    for event in events:
        match event["kind"]:
            case "armies_placed":
                territory = event["territory"]
                armies[territory] = armies.get(territory, 1) + int(event["count"])
            case "battle_resolved":
                source, target = event["from_territory"], event["to_territory"]
                armies[source] = armies.get(source, 1) - int(event["outcome"]["attacker_losses"])
                armies[target] = armies.get(target, 1) - int(event["outcome"]["defender_losses"])
            case "fortified":
                source, target = event["from_territory"], event["to_territory"]
                armies[source] = armies.get(source, 1) - int(event["count"])
                armies[target] = armies.get(target, 1) + int(event["count"])


def _attack_outcome(events: list[dict[str, Any]]) -> tuple[int, bool]:
    attacker_losses = sum(
        int(event["outcome"]["attacker_losses"]) for event in events if event["kind"] == "battle_resolved"
    )
    conquered = any(event["kind"] == "territory_conquered" for event in events)
    return attacker_losses, conquered


def _following_events(occurrences: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    # an attack's battle events are written right after it, before any thought or message
    for record in occurrences[index + 1 :]:
        if record["type"] == "events":
            return record["events"]
        if record["type"] == "action":
            return []
    return []


def _following_occupy_amount(occurrences: list[dict[str, Any]], index: int) -> int | None:
    # the occupy is the conqueror's next action; thought/message records may sit in between
    for record in occurrences[index + 1 :]:
        if record["type"] == "action":
            return int(record["action"]["armies_to_move"]) if record["action"]["kind"] == "occupy" else None
    return None


if __name__ == "__main__":
    main()
