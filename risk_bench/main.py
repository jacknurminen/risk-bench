import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from risk_bench.agents.anthropic import AnthropicAgent
from risk_bench.agents.base import Agent
from risk_bench.agents.google import GeminiAgent
from risk_bench.agents.models import AnthropicModel, GoogleModel, OpenAIModel
from risk_bench.agents.openai import OpenAIAgent
from risk_bench.config import Settings, load_settings
from risk_bench.game.types import PlayerId
from risk_bench.players.base import Player
from risk_bench.players.llm import LLMPlayer
from risk_bench.players.random import RandomPlayer
from risk_bench.runner.game_runner import GameRecord, GameRunner
from risk_bench.runner.journal import SCHEMA_VERSION, JournalHeader, JournalWriter, restore_game
from risk_bench.tournament.results import TournamentReport, write_report
from risk_bench.tournament.runner import PlayerFactory, TournamentRunner

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
    verbose: Annotated[bool, typer.Option(help="Print events and chat as they happen.")] = True,
) -> None:
    """Run a single game between the given players (or resume a crashed one) and print the outcome."""
    settings = load_settings()
    if resume is not None:
        record, journal_path = _resume_game(journal_file=resume, settings=settings, verbose=verbose)
    elif models:
        record, journal_path = _run_new_game(models=models, seed=seed, settings=settings, verbose=verbose)
    else:
        raise typer.BadParameter("Provide player specs, or --resume with a journal file.")

    outcome = f"[bold green]{record.winner} wins[/bold green]" if record.winner else "[bold yellow]draw[/bold yellow]"
    console.print(f"\nGame over after {record.turn_count} turns: {outcome}")
    for player_id, mission in record.missions.items():
        console.print(f"  {player_id}: {mission.description}")
    console.print(f"Journal: {journal_path}")


def _run_new_game(models: list[str], seed: int, settings: Settings, verbose: bool) -> tuple[GameRecord, Path]:
    specs = _named_specs(specs=models)
    players = {
        name: _player_factory(spec=spec, settings=settings)(name, random.Random(f"{seed}:{name}"))
        for name, spec in specs.items()
    }
    started_at = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    journal_path = settings.output_directory / "games" / f"{started_at}-seed{seed}.jsonl"
    journal = JournalWriter(path=journal_path, append=False)
    journal.write_header(
        header=JournalHeader(
            schema_version=SCHEMA_VERSION,
            seed=seed,
            player_specs=specs,
            max_turns=settings.max_turns,
            allow_private_messages=settings.allow_private_messages,
        )
    )
    runner = GameRunner(
        players=players,
        seed=seed,
        max_turns=settings.max_turns,
        allow_private_messages=settings.allow_private_messages,
        journal=journal,
        verbose=verbose,
    )
    try:
        return runner.run(), journal_path
    finally:
        journal.close()


def _resume_game(journal_file: Path, settings: Settings, verbose: bool) -> tuple[GameRecord, Path]:
    restored = restore_game(path=journal_file)
    if restored.is_finished:
        raise typer.BadParameter(f"{journal_file} records a finished game; nothing to resume.")
    header = restored.header
    players = {
        name: _player_factory(spec=spec, settings=settings)(name, random.Random(f"{header.seed}:{name}:resume"))
        for name, spec in header.player_specs.items()
    }
    journal = JournalWriter(path=journal_file, append=True)
    runner = GameRunner(
        players=players,
        seed=header.seed,
        max_turns=header.max_turns,
        allow_private_messages=header.allow_private_messages,
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
    specs = _named_specs(specs=models)
    runner = TournamentRunner(
        participants={name: _player_factory(spec=spec, settings=settings) for name, spec in specs.items()},
        participant_specs=specs,
        journal_directory=settings.output_directory / "games",
        games=games,
        players_per_game=players_per_game,
        base_seed=seed,
        max_turns=settings.max_turns,
        allow_private_messages=settings.allow_private_messages,
        k_factor=settings.elo_k_factor,
        verbose=verbose,
    )
    report = runner.run()
    write_report(report=report, path=report_path)
    _print_standings(report=report)
    console.print(f"\nFull report written to {report_path}")


_TIER_MODELS_BY_PROVIDER: dict[str, type[AnthropicModel] | type[GoogleModel] | type[OpenAIModel]] = {
    "anthropic": AnthropicModel,
    "google": GoogleModel,
    "openai": OpenAIModel,
}


def _named_specs(specs: list[str]) -> dict[str, str]:
    named = {}
    for spec in specs:
        base_name = "random" if spec == "random" else _resolve_model(spec=spec)
        name = base_name
        suffix = 2
        while name in named:
            name = f"{base_name}#{suffix}"
            suffix += 1
        named[name] = spec
    return named


def _player_factory(spec: str, settings: Settings) -> PlayerFactory:
    if spec == "random":
        return lambda player_id, rng: RandomPlayer(player_id=player_id, rng=rng)
    agent = _build_agent(spec=spec, settings=settings)

    def create_llm_player(player_id: PlayerId, rng: random.Random) -> Player:
        return LLMPlayer(player_id=player_id, agent=agent, max_invalid_retries=settings.max_invalid_retries, rng=rng)

    return create_llm_player


def _resolve_model(spec: str) -> str:
    provider, _, model_or_tier = spec.partition(":")
    if not model_or_tier:
        raise typer.BadParameter(f"'{spec}' is not 'random' or a provider:tier / provider:model spec.")
    tier_models = _TIER_MODELS_BY_PROVIDER.get(provider)
    if tier_models is None:
        raise typer.BadParameter(f"Unknown provider '{provider}'; use anthropic, openai, or google.")
    if model_or_tier.upper() in tier_models.__members__:
        return tier_models[model_or_tier.upper()].value
    return model_or_tier


def _build_agent(spec: str, settings: Settings) -> Agent:
    provider = spec.partition(":")[0]
    model = _resolve_model(spec=spec)
    api_key = _require_api_key(provider=provider, settings=settings)
    match provider:
        case "anthropic":
            return AnthropicAgent(model=model, api_key=api_key, max_tokens=settings.max_agent_tokens)
        case "openai":
            return OpenAIAgent(model=model, api_key=api_key, max_tokens=settings.max_agent_tokens)
        case "google":
            return GeminiAgent(model=model, api_key=api_key, max_tokens=settings.max_agent_tokens)
        case unknown_provider:
            raise typer.BadParameter(f"Unknown provider '{unknown_provider}'; use anthropic, openai, or google.")


def _require_api_key(provider: str, settings: Settings) -> str:
    keys_by_provider = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "google": settings.google_api_key,
    }
    api_key = keys_by_provider.get(provider)
    if api_key is None:
        raise typer.BadParameter(f"No API key configured for '{provider}'; set {provider.upper()}_API_KEY.")
    return api_key


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
