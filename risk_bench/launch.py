"""Running games as plain function calls, shared by the CLI and the web app's worker processes."""

import random
from datetime import UTC, datetime
from pathlib import Path

from risk_bench import specs
from risk_bench.config import Settings
from risk_bench.runner.game_runner import GameRecord, GameRunner
from risk_bench.runner.journal import SCHEMA_VERSION, JournalHeader, JournalWriter, restore_game


def run_new_game(
    models: list[str],
    seed: int,
    settings: Settings,
    commentator_spec: str | None,
    journal_path: Path | None,
    max_turns: int | None,
    verbose: bool,
) -> tuple[GameRecord, Path]:
    """Play a fully journaled game between the given player specs.

    Args:
        models: Player specs in seating order ('random', 'provider:tier', 'provider:model').
        seed: The game seed; the whole game is reproducible from it.
        settings: Provides API keys, agent limits, and game defaults.
        commentator_spec: Model spec for the omniscient commentator, or None for none.
        journal_path: Where to write the journal; None derives a timestamped path.
        max_turns: Turn limit; None uses the settings default.
        verbose: Whether to print events and chat to stdout as they happen.

    Returns:
        The finished game's record and its journal path.

    Raises:
        ValueError: If any spec is invalid or a needed API key is missing.
    """
    player_specs = specs.named_specs(specs=models)
    players = {
        name: specs.player_factory(spec=spec, settings=settings)(name, random.Random(f"{seed}:{name}"))
        for name, spec in player_specs.items()
    }
    if journal_path is None:
        started_at = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
        journal_path = settings.output_directory / "games" / f"{started_at}-seed{seed}.jsonl"
    turn_limit = max_turns if max_turns is not None else settings.max_turns
    journal = JournalWriter(path=journal_path, append=False)
    journal.write_header(
        header=JournalHeader(
            schema_version=SCHEMA_VERSION,
            seed=seed,
            player_specs=player_specs,
            max_turns=turn_limit,
            allow_private_messages=settings.allow_private_messages,
            max_negotiation_rounds=settings.max_negotiation_rounds,
        )
    )
    runner = GameRunner(
        players=players,
        seed=seed,
        max_turns=turn_limit,
        allow_private_messages=settings.allow_private_messages,
        max_negotiation_rounds=settings.max_negotiation_rounds,
        commentator=specs.build_commentator(spec=commentator_spec, settings=settings),
        journal=journal,
        verbose=verbose,
    )
    try:
        return runner.run(), journal_path
    finally:
        journal.close()


def resume_game(journal_file: Path, settings: Settings, commentator_spec: str | None, verbose: bool) -> GameRecord:
    """Continue a paused or crashed game from its journal until completion.

    Args:
        journal_file: The journal of the unfinished game.
        settings: Provides API keys and agent limits.
        commentator_spec: Model spec for the commentator, or None for none.
        verbose: Whether to print events and chat to stdout as they happen.

    Returns:
        The finished game's record.

    Raises:
        ValueError: If the journal records a finished game or a spec is invalid.
    """
    restored = restore_game(path=journal_file)
    if restored.is_finished:
        raise ValueError(f"{journal_file} records a finished game; nothing to resume.")
    header = restored.header
    players = {
        name: specs.player_factory(spec=spec, settings=settings)(name, random.Random(f"{header.seed}:{name}:resume"))
        for name, spec in header.player_specs.items()
    }
    journal = JournalWriter(path=journal_file, append=True)
    runner = GameRunner(
        players=players,
        seed=header.seed,
        max_turns=header.max_turns,
        allow_private_messages=header.allow_private_messages,
        max_negotiation_rounds=header.max_negotiation_rounds,
        commentator=specs.build_commentator(spec=commentator_spec, settings=settings),
        journal=journal,
        verbose=verbose,
    )
    try:
        return runner.resume(restored=restored)
    finally:
        journal.close()
