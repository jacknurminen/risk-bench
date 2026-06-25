from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from risk_bench import launch, specs
from risk_bench.api.app import create_app
from risk_bench.config import load_settings
from risk_bench.players.llm import ProviderUnavailableError
from risk_bench.runner.evaluation_store import evaluation_header, read_evaluation
from risk_bench.tournament.results import TournamentReport, write_report
from risk_bench.tournament.runner import TournamentRunner
from risk_bench.viewer.html import render_viewer_html
from risk_bench.viewer.snapshots import build_view_model

app = typer.Typer(help="Risk-Bench: an LLM benchmark based on the board game Risk.")
console = Console()

MODELS_HELP = (
    "Players as provider:tier specs like 'anthropic:hi' (providers: anthropic/openai/google, tiers: lo/mid/hi), "
    "provider:model for an explicit model id, or 'random' for the baseline. Add ':<effort>' (low/medium/high) "
    "to set a player's reasoning effort, e.g. 'anthropic:hi:low'."
)


@app.command()
def game(
    models: Annotated[list[str] | None, typer.Argument(help=MODELS_HELP)] = None,
    seed: Annotated[int, typer.Option(help="Random seed; the whole game is reproducible from it.")] = 0,
    resume: Annotated[Path | None, typer.Option(help="Resume a crashed game from its journal file.")] = None,
    commentator: Annotated[
        list[str] | None,
        typer.Option(
            help="Commentator panel member as a model spec (provider:size:effort), repeatable to add "
            "more members; every member annotates the same game and all their tags are kept."
        ),
    ] = None,
    journal: Annotated[Path | None, typer.Option(help="Journal file path; defaults to a timestamped name.")] = None,
    max_turns: Annotated[int | None, typer.Option(help="Turn limit override; defaults to settings.")] = None,
    verbose: Annotated[bool, typer.Option(help="Print events and chat as they happen.")] = True,
) -> None:
    """Run a single game between the given players (or resume a crashed one) and print the outcome."""
    settings = load_settings()
    try:
        commentators = commentator or []
        if resume is not None:
            record = launch.resume_game(
                journal_file=resume, settings=settings, commentators=commentators, verbose=verbose
            )
            journal_path = resume
        elif models:
            record, journal_path = launch.run_new_game(
                models=models,
                seed=seed,
                settings=settings,
                commentators=commentators,
                journal_path=journal,
                max_turns=max_turns,
                verbose=verbose,
            )
        else:
            raise typer.BadParameter("Provide player specs, or --resume with a journal file.")
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    except ProviderUnavailableError as error:
        console.print(f"\n[bold yellow]Game paused:[/bold yellow] {error}")
        console.print("Resume it later with: game --resume <journal>")
        raise typer.Exit(code=1) from None

    outcome = f"[bold green]{record.winner} wins[/bold green]" if record.winner else "[bold yellow]draw[/bold yellow]"
    console.print(f"\nGame over after {record.turn_count} turns: {outcome}")
    for player_id, mission in record.missions.items():
        console.print(f"  {player_id}: {mission.description}")
    console.print(f"Journal: {journal_path}")


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
            name: specs.player_factory(spec=spec, settings=settings, trace=None) for name, spec in player_specs.items()
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
    html = render_viewer_html(view_model=build_view_model(journal_path=journal), trace_url="")
    destination = output if output is not None else journal.with_suffix(".html")
    destination.write_text(html, encoding="utf-8")
    console.print(f"Viewer written to {destination}")


@app.command()
def evaluate(
    journal: Annotated[Path, typer.Argument(help="Game journal (.jsonl) to evaluate; need not be finished.")],
    evaluator: Annotated[
        list[str] | None,
        typer.Option(
            help="Evaluator panel member as a model spec (provider:size:effort), repeatable to add more "
            "members; every member reads the whole game and all their output is kept for agreement."
        ),
    ] = None,
) -> None:
    """Run a whole-game evaluator panel over a stored game and write its evaluation sidecar."""
    settings = load_settings()
    try:
        path = launch.run_evaluation(journal_path=journal, evaluator_specs=evaluator or [], settings=settings)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    _print_evaluation_summary(records=read_evaluation(path=path))
    console.print(f"Evaluation written to {path}")


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


def _print_evaluation_summary(records: list[dict[str, Any]]) -> None:
    header = evaluation_header(records=records)
    if header is None:
        console.print("[bold yellow]No evaluation was produced[/bold yellow] (every panel member failed).")
        return
    counts = {kind: sum(1 for record in records if record.get("type") == kind) for kind in _EVAL_RECORD_KINDS}
    total_citations, broken_citations = _citation_integrity(records=records)
    console.print(f"\nPanel: [bold]{', '.join(header.panel)}[/bold]")
    console.print(
        f"Tags: {counts['eval_tag']}  Arcs: {counts['eval_arc']}  Syntheses: {counts['eval_synthesis']}  "
        f"(covers {header.span_count} spans across {header.record_count} records)"
    )
    console.print(f"Broken citations: {broken_citations}/{total_citations}")


_EVAL_RECORD_KINDS = ("eval_tag", "eval_arc", "eval_synthesis")


def _citation_integrity(records: list[dict[str, Any]]) -> tuple[int, int]:
    total, broken = 0, 0
    for record in records:
        for evidence in _record_evidence(record=record):
            total += 1
            broken += 0 if evidence.get("resolved") else 1
    return total, broken


def _record_evidence(record: dict[str, Any]) -> list[dict[str, Any]]:
    match record.get("type"):
        case "eval_tag":
            return list(record.get("evidence", []))
        case "eval_arc":
            return [record["antecedent"], record["consequent"]]
        case "eval_synthesis":
            from_profile = [item for dimension in record.get("profile", []) for item in dimension.get("evidence", [])]
            return from_profile + list(record.get("key_episodes", []))
        case _:
            return []


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
