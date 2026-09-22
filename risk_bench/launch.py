"""Running games as plain function calls, shared by the CLI and the web app's worker processes."""

import hashlib
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from risk_bench import analysis, specs
from risk_bench.agents.tracing import TraceWriter, trace_path_for_journal
from risk_bench.config import Settings
from risk_bench.game.rules import reinforcement_count
from risk_bench.game.state import GameState
from risk_bench.players import rendering, tools
from risk_bench.players.human import mailbox_for
from risk_bench.players.rendering import render_board_text
from risk_bench.runner.evaluation import EvaluatedGame, Evaluator
from risk_bench.runner.evaluation_store import (
    EvaluationWriter,
    evaluation_path_for_journal,
    evaluation_trace_path_for_journal,
)
from risk_bench.runner.evaluation_versions import archive_current
from risk_bench.runner.game_runner import GameRecord, GameRunner
from risk_bench.runner.journal import SCHEMA_VERSION, JournalHeader, JournalWriter, read_journal, restore_game
from risk_bench.runner.trace_index import TraceSpan, build_trace_index, living_players_by_turn


def run_new_game(
    models: list[str],
    seed: int,
    settings: Settings,
    commentators: list[str],
    journal_path: Path | None,
    max_turns: int | None,
    verbose: bool,
    human_controls: bool = False,
) -> tuple[GameRecord, Path]:
    """Play a fully journaled game between the given player specs.

    Args:
        models: Player specs ('random', 'provider:tier', 'provider:model'); their input order
            does not fix the seating, which is randomly shuffled (seed-derived) before play.
        seed: The engine seed; fresh model responses are not seed-deterministic.
        settings: Provides API keys, agent limits, and game defaults.
        commentators: The commentator panel as one model spec per member; empty for none.
        journal_path: Where to write the journal; None derives a timestamped path.
        max_turns: Turn limit; None uses the settings default.
        verbose: Whether to print events and chat to stdout as they happen.
        human_controls: True only for web workers with an API serving the human mailbox.

    Returns:
        The finished game's record and its journal path.

    Raises:
        ValueError: If any spec is invalid or a needed API key is missing.
    """
    if "human" in models and not human_controls:
        raise ValueError("Launch human games from the web app (serve), where the play controls are available.")
    player_specs = _seat_in_random_order(player_specs=specs.named_specs(specs=models), seed=seed)
    if journal_path is None:
        started_at = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
        journal_path = settings.output_directory / "games" / f"{started_at}-seed{seed}" / "game.jsonl"
    trace = _trace_writer(
        journal_path=journal_path, player_specs=player_specs, commentators=commentators, append=False
    )
    players = {
        name: specs.player_factory(spec=spec, settings=settings, trace=trace, human_mailbox=mailbox_for(journal_path))(
            name, random.Random(f"{seed}:{name}")
        )
        for name, spec in player_specs.items()
    }
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
            commentators=commentators,
            configuration=persisted_configuration(settings),
        )
    )
    runner = GameRunner(
        players=players,
        seed=seed,
        max_turns=turn_limit,
        allow_private_messages=settings.allow_private_messages,
        max_negotiation_rounds=settings.max_negotiation_rounds,
        commentators=specs.build_commentators(commentator_specs=commentators, settings=settings, trace=trace),
        max_concurrent_commentators=settings.max_concurrent_commentators,
        journal=journal,
        trace=trace,
        verbose=verbose,
    )
    try:
        return runner.run(), journal_path
    finally:
        journal.close()
        if trace is not None:
            trace.close()


def resume_game(
    journal_file: Path, settings: Settings, commentators: list[str], verbose: bool, human_controls: bool = False
) -> GameRecord:
    """Continue a paused or crashed game from its journal until completion.

    Args:
        journal_file: The journal of the unfinished game.
        settings: Provides API keys and agent limits.
        commentators: The commentator panel as one model spec per member; empty for none.
        verbose: Whether to print events and chat to stdout as they happen.
        human_controls: True only for web workers with an API serving the human mailbox.

    Returns:
        The finished game's record.

    Raises:
        ValueError: If the journal records a finished game or a spec is invalid.
    """
    restored = restore_game(path=journal_file)
    if restored.is_finished:
        raise ValueError(f"{journal_file} records a finished game; nothing to resume.")
    header = restored.header
    stored_settings = header.configuration.get("settings", {})
    if stored_settings:
        settings = settings.model_copy(
            update={
                **stored_settings,
                "agent_reasoning_effort": settings.agent_reasoning_effort.__class__(
                    stored_settings["agent_reasoning_effort"]
                ),
            }
        )
    if header.configuration:
        commentators = header.commentators
    for limitation in restored.recovery_limitations:
        print(f"[resume] {limitation}", file=sys.stderr)
    if "human" in header.player_specs.values() and not human_controls:
        raise ValueError("Resume human games from the web app (serve).")
    trace = _trace_writer(
        journal_path=journal_file, player_specs=header.player_specs, commentators=commentators, append=True
    )
    players = {
        name: specs.player_factory(spec=spec, settings=settings, trace=trace, human_mailbox=mailbox_for(journal_file))(
            name, random.Random(f"{header.seed}:{name}")
        )
        for name, spec in header.player_specs.items()
    }
    journal = JournalWriter(path=journal_file, append=True)
    runner = GameRunner(
        players=players,
        seed=header.seed,
        max_turns=header.max_turns,
        allow_private_messages=header.allow_private_messages,
        max_negotiation_rounds=header.max_negotiation_rounds,
        commentators=specs.build_commentators(commentator_specs=commentators, settings=settings, trace=trace),
        max_concurrent_commentators=settings.max_concurrent_commentators,
        journal=journal,
        trace=trace,
        verbose=verbose,
    )
    try:
        return runner.resume(restored=restored)
    finally:
        journal.close()
        if trace is not None:
            trace.close()


def run_evaluation(journal_path: Path, evaluator_specs: list[str], settings: Settings) -> Path:
    """Evaluate a stored game with a panel of whole-game evaluators, writing the eval sidecar.

    Reads the journal (which may be a paused game's prefix), builds the trace index the panel
    cites, runs every member offline and concurrently over the same trace, and writes their
    tags and syntheses to ``game.eval.jsonl`` — overwriting any prior run, since the
    evaluation is a re-runnable derived artifact, never part of the canonical journal.

    Args:
        journal_path: The stored game journal to evaluate; need not be a finished game.
        evaluator_specs: One model spec per panel member, in panel order; must be non-empty.
        settings: Provides API keys and the token limit.

    Returns:
        The path to the written evaluation sidecar.

    Raises:
        ValueError: If no members are given, a spec is invalid, or the journal is unreadable.
    """
    if not evaluator_specs:
        raise ValueError("Provide at least one evaluator panel member.")
    records = read_journal(path=journal_path)
    restored = restore_game(path=journal_path)
    spans = build_trace_index(records=records)
    overview = _render_game_overview(state=restored.state)
    # the evaluator sweeps every turn present in the trace and must consider every player alive on it
    turns = sorted({span.turn for span in spans})
    living_by_turn = living_players_by_turn(records=records, player_ids=set(restored.state.players), turns=turns)
    # keep the previous run: archive it aside before the flat sidecar paths are overwritten, so this
    # run becomes a new version rather than replacing the old one
    archive_current(journal_path=journal_path)
    # the panel's API calls go to their own trace sidecar, so a re-run overwrites cleanly rather
    # than appending duplicate evaluator calls into the game's own trace
    trace = TraceWriter(path=evaluation_trace_path_for_journal(journal_path=journal_path), append=False)
    trace.set_context(turn=restored.state.turn_number, kind="evaluation")
    try:
        evaluators = specs.build_evaluators(evaluator_specs=evaluator_specs, settings=settings, trace=trace)
        evaluations = _gather_evaluations(
            evaluators=evaluators,
            players=set(restored.state.players),
            spans=spans,
            overview=overview,
            living_by_turn=living_by_turn,
            max_workers=min(settings.max_concurrent_commentators, len(evaluators)),
        )
    finally:
        trace.close()

    writer = EvaluationWriter(path=evaluation_path_for_journal(journal_path=journal_path))
    try:
        writer.write_header(panel=evaluator_specs, record_count=len(records), span_count=len(spans))
        for index, result in enumerate(evaluations):
            if result is not None:
                writer.write_evaluation(evaluator_index=index, evaluation=result.evaluation)
                for coverage in result.coverage:
                    writer.write_coverage(evaluator_index=index, coverage=coverage)
    finally:
        writer.close()

    # the analysis reads the sidecar we just closed and writes its own derived artifact; a UI-run
    # evaluation therefore also produces the analysis dump
    _write_analysis_dump(journal_path=journal_path)
    return writer.path


def _write_analysis_dump(journal_path: Path) -> None:
    # the analysis is a derived convenience computed after the fact; a failure here must never
    # discard the evaluation that was just written successfully
    try:
        analysis.write_analysis(
            journal_path=journal_path,
            eval_path=evaluation_path_for_journal(journal_path=journal_path),
            analysis_path=analysis.analysis_path_for_journal(journal_path=journal_path),
        )
    except Exception as error:  # noqa: BLE001 - the analysis must never break a good evaluation run
        print(f"[analysis-error] {error}", file=sys.stderr)


def _gather_evaluations(
    evaluators: tuple[Evaluator, ...],
    players: set[str],
    spans: tuple[TraceSpan, ...],
    overview: str,
    living_by_turn: dict[int, frozenset[str]],
    max_workers: int,
) -> list[EvaluatedGame | None]:
    if not evaluators:
        return []
    # written in panel order regardless of which provider answered first, so the sidecar is stable
    results: list[EvaluatedGame | None] = [None] * len(evaluators)
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {
            pool.submit(
                evaluator.evaluate_game,
                players=players,
                spans=spans,
                game_overview=overview,
                living_by_turn=living_by_turn,
            ): index
            for index, evaluator in enumerate(evaluators)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as error:  # noqa: BLE001 - one member must never abort the panel
                print(f"[evaluator[{index}]-error] {error}", file=sys.stderr)
    return results


def _render_game_overview(state: GameState) -> str:
    lines = []
    for player_id, player in state.players.items():
        if player.eliminated_by is not None:
            lines.append(
                f"- {player_id}: ELIMINATED by {player.eliminated_by} | SECRET MISSION: {player.mission.description}"
            )
            continue
        lines.append(
            f"- {player_id}: {len(state.territories_owned_by(player_id=player_id))} territories, "
            f"{state.army_count(player_id=player_id)} armies, "
            f"income {reinforcement_count(state=state, player_id=player_id)}/turn, "
            f"{len(player.cards)} cards | SECRET MISSION: {player.mission.description}"
        )
    outcome = (
        f"{state.winner} won the game." if state.winner else "The game ended without a winner (draw or unfinished)."
    )
    standings = "Final omniscient standings (you see every secret mission):\n" + "\n".join(lines)
    return f"{standings}\n\n{outcome}\n\n{render_board_text(owner=dict(state.owner), armies=dict(state.armies))}"


def _seat_in_random_order(player_specs: dict[str, str], seed: int) -> dict[str, str]:
    """Return ``player_specs`` reordered into a seed-derived random seat order.

    The dict's order is the turn order the game is set up with, and turn order is a real
    advantage in Risk. Randomising it here keeps the order the specs were given on the command
    line from biasing who goes first, while deriving the shuffle from the game seed (and writing
    the resulting order into the journal header) keeps the whole game reproducible and replayable.

    Args:
        player_specs: Player name mapped to its spec, in the order the specs were given.
        seed: The game seed the shuffle is derived from.

    Returns:
        The same mapping reordered into the randomised seat order.
    """
    seats = list(player_specs)
    random.Random(seed).shuffle(seats)
    return {name: player_specs[name] for name in seats}


def _trace_writer(
    journal_path: Path, player_specs: dict[str, str], commentators: list[str], append: bool
) -> TraceWriter | None:
    involves_an_api = bool(commentators) or any(spec not in {"random", "human"} for spec in player_specs.values())
    if not involves_an_api:
        return None
    return TraceWriter(path=trace_path_for_journal(journal_path=journal_path), append=append)


def persisted_configuration(settings: Settings) -> dict:
    """Allowlist experimental settings; credentials and environment paths never enter journals."""
    sources = {"rendering": Path(rendering.__file__).read_text(), "tools": Path(tools.__file__).read_text()}
    package = Path(__file__).parent
    code_files = sorted(
        [
            *(package / "game").glob("*.py"),
            *(package / "players").glob("*.py"),
            package / "runner" / "game_runner.py",
            package / "runner" / "checkpoint.py",
        ]
    )
    code_version = hashlib.sha256(
        b"".join(str(path.relative_to(package)).encode() + b"\0" + path.read_bytes() for path in code_files)
    ).hexdigest()
    return {
        "settings": settings.model_dump(
            mode="json",
            include={
                "max_agent_tokens",
                "agent_reasoning_effort",
                "max_invalid_retries",
                "max_consecutive_provider_failures",
                "max_concurrent_commentators",
            },
        ),
        "prompt_and_tools": sources,
        "prompt_version": hashlib.sha256(sources["rendering"].encode()).hexdigest(),
        "engine_version": 1,
        "code_version": code_version,
        "rng_version": "python-mt19937-v3",
    }
