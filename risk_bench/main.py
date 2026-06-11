import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from risk_bench import specs
from risk_bench.api.app import create_app
from risk_bench.config import Settings, load_settings
from risk_bench.runner.commentary import Commentator
from risk_bench.runner.game_runner import GameRecord, GameRunner
from risk_bench.runner.journal import SCHEMA_VERSION, JournalHeader, JournalWriter, restore_game
from risk_bench.tournament.results import TournamentReport, write_report
from risk_bench.tournament.runner import TournamentRunner
from risk_bench.viewer.html import render_viewer_html
from risk_bench.viewer.snapshots import build_view_model

app = typer.Typer(help="Risk-Bench: an LLM benchmark based on the board game Risk.")
console = Console()

MODELS_HELP = (
    "Players as provider:tier specs like 'anthropic:hi' (providers: anthropic/openai/google, tiers: lo/mid/hi), "
    "provider:model for an explicit model id, or 'random' for the baseline."
)


@app.command()
def game(
    models: Annotated[list[str] | None, typer.Argument(help=MODELS_HELP)] = None,
    seed: Annotated[int, typer.Option(help="Random seed; the whole game is reproducible from it.")] = 0,
    resume: Annotated[Path | None, typer.Option(help="Resume a crashed game from its journal file.")] = None,
    commentator: Annotated[
        str | None,
        typer.Option(help="Model spec for an omniscient commentator that comments after each turn."),
    ] = None,
    journal: Annotated[Path | None, typer.Option(help="Journal file path; defaults to a timestamped name.")] = None,
    max_turns: Annotated[int | None, typer.Option(help="Turn limit override; defaults to settings.")] = None,
    verbose: Annotated[bool, typer.Option(help="Print events and chat as they happen.")] = True,
) -> None:
    """Run a single game between the given players (or resume a crashed one) and print the outcome."""
    settings = load_settings()
    try:
        commentator_instance = specs.build_commentator(spec=commentator, settings=settings)
        if resume is not None:
            record, journal_path = _resume_game(
                journal_file=resume, settings=settings, commentator=commentator_instance, verbose=verbose
            )
        elif models:
            record, journal_path = _run_new_game(
                models=models,
                seed=seed,
                settings=settings,
                commentator=commentator_instance,
                journal_path=journal,
                max_turns=max_turns if max_turns is not None else settings.max_turns,
                verbose=verbose,
            )
        else:
            raise typer.BadParameter("Provide player specs, or --resume with a journal file.")
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None

    outcome = f"[bold green]{record.winner} wins[/bold green]" if record.winner else "[bold yellow]draw[/bold yellow]"
    console.print(f"\nGame over after {record.turn_count} turns: {outcome}")
    for player_id, mission in record.missions.items():
        console.print(f"  {player_id}: {mission.description}")
    console.print(f"Journal: {journal_path}")


def _run_new_game(
    models: list[str],
    seed: int,
    settings: Settings,
    commentator: Commentator | None,
    journal_path: Path | None,
    max_turns: int,
    verbose: bool,
) -> tuple[GameRecord, Path]:
    player_specs = specs.named_specs(specs=models)
    players = {
        name: specs.player_factory(spec=spec, settings=settings)(name, random.Random(f"{seed}:{name}"))
        for name, spec in player_specs.items()
    }
    if journal_path is None:
        started_at = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
        journal_path = settings.output_directory / "games" / f"{started_at}-seed{seed}.jsonl"
    journal = JournalWriter(path=journal_path, append=False)
    journal.write_header(
        header=JournalHeader(
            schema_version=SCHEMA_VERSION,
            seed=seed,
            player_specs=player_specs,
            max_turns=max_turns,
            allow_private_messages=settings.allow_private_messages,
            max_negotiation_rounds=settings.max_negotiation_rounds,
        )
    )
    runner = GameRunner(
        players=players,
        seed=seed,
        max_turns=max_turns,
        allow_private_messages=settings.allow_private_messages,
        max_negotiation_rounds=settings.max_negotiation_rounds,
        commentator=commentator,
        journal=journal,
        verbose=verbose,
    )
    try:
        return runner.run(), journal_path
    finally:
        journal.close()


def _resume_game(
    journal_file: Path, settings: Settings, commentator: Commentator | None, verbose: bool
) -> tuple[GameRecord, Path]:
    restored = restore_game(path=journal_file)
    if restored.is_finished:
        raise typer.BadParameter(f"{journal_file} records a finished game; nothing to resume.")
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
        commentator=commentator,
        journal=journal,
        verbose=verbose,
    )
    try:
        return runner.resume(restored=restored), journal_file
    finally:
        journal.close()


@app.command()
def tournament(
    models: Annotated[list[str], typer.Argument(help=MODELS_HELP)],
    games: Annotated[int, typer.Option(help="Number of games to play.")] = 10,
    players_per_game: Annotated[int, typer.Option(help="Players seated per game (3-6).")] = 3,
    seed: Annotated[int, typer.Option(help="Base seed; per-game seeds are derived from it.")] = 0,
    report_path: Annotated[Path, typer.Option(help="Where to write the JSON report.")] = Path("data/tournament.json"),
    verbose: Annotated[bool, typer.Option(help="Print events and chat as they happen.")] = False,
) -> None:
    """Run a tournament, print final standings, and write the JSON report."""
    settings = load_settings()
    try:
        player_specs = specs.named_specs(specs=models)
        participants = {
            name: specs.player_factory(spec=spec, settings=settings) for name, spec in player_specs.items()
        }
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    runner = TournamentRunner(
        participants=participants,
        participant_specs=player_specs,
        journal_directory=settings.output_directory / "games",
        games=games,
        players_per_game=players_per_game,
        base_seed=seed,
        max_turns=settings.max_turns,
        allow_private_messages=settings.allow_private_messages,
        max_negotiation_rounds=settings.max_negotiation_rounds,
        k_factor=settings.elo_k_factor,
        verbose=verbose,
    )
    report = runner.run()
    write_report(report=report, path=report_path)
    _print_standings(report=report)
    console.print(f"\nFull report written to {report_path}")


@app.command()
def view(
    journal: Annotated[Path, typer.Argument(help="Game journal (.jsonl) to render.")],
    output: Annotated[Path | None, typer.Option(help="Output HTML path; defaults to alongside the journal.")] = None,
) -> None:
    """Render a game journal into a self-contained HTML replay viewer."""
    html = render_viewer_html(view_model=build_view_model(journal_path=journal))
    destination = output if output is not None else journal.with_suffix(".html")
    destination.write_text(html, encoding="utf-8")
    console.print(f"Viewer written to {destination}")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to bind to.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind to.")] = 8420,
) -> None:
    """Serve the web app: browse games, watch them live, and launch new ones."""
    settings = load_settings()
    web_app = create_app(games_directory=settings.output_directory / "games")
    console.print(f"risk-bench running at [bold]http://{host}:{port}[/bold] — Ctrl+C to stop.")
    uvicorn.run(web_app, host=host, port=port, log_level="warning")


def _print_standings(report: TournamentReport) -> None:
    table = Table(title=f"Standings after {report.games} games")
    table.add_column("Agent")
    table.add_column("Elo", justify="right")
    table.add_column("Games", justify="right")
    table.add_column("Wins", justify="right")
    table.add_column("Draws", justify="right")
    table.add_column("Invalid actions", justify="right")
    for standing in report.standings:
        table.add_row(
            standing.name,
            f"{standing.rating:.0f}",
            str(standing.games_played),
            str(standing.wins),
            str(standing.draws),
            str(standing.invalid_actions),
        )
    console.print(table)


if __name__ == "__main__":
    app()
