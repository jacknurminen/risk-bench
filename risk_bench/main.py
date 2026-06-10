import random
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from risk_bench.agents.anthropic import AnthropicAgent
from risk_bench.agents.base import Agent
from risk_bench.agents.google import GeminiAgent
from risk_bench.agents.openai import OpenAIAgent
from risk_bench.agents.xai import GrokAgent
from risk_bench.config import Settings, load_settings
from risk_bench.game.types import PlayerId
from risk_bench.players.base import Player
from risk_bench.players.llm import LLMPlayer
from risk_bench.players.random import RandomPlayer
from risk_bench.runner.game_runner import GameRunner
from risk_bench.tournament.results import TournamentReport, write_report
from risk_bench.tournament.runner import PlayerFactory, TournamentRunner

app = typer.Typer(help="Risk-Bench: an LLM benchmark based on the board game Risk.")
console = Console()

MODELS_HELP = "Players as provider:model specs (anthropic/openai/google/xai), or 'random' for the baseline."


@app.command()
def game(
    models: Annotated[list[str], typer.Argument(help=MODELS_HELP)],
    seed: Annotated[int, typer.Option(help="Random seed; the whole game is reproducible from it.")] = 0,
    verbose: Annotated[bool, typer.Option(help="Print events and chat as they happen.")] = True,
) -> None:
    """Run a single game between the given players and print the outcome."""
    settings = load_settings()
    factories = _named_factories(specs=models, settings=settings)
    players = {name: factory(name, random.Random(f"{seed}:{name}")) for name, factory in factories.items()}
    record = GameRunner(
        players=players,
        seed=seed,
        max_turns=settings.max_turns,
        allow_private_messages=settings.allow_private_messages,
        verbose=verbose,
    ).run()

    outcome = f"[bold green]{record.winner} wins[/bold green]" if record.winner else "[bold yellow]draw[/bold yellow]"
    console.print(f"\nGame over after {record.turn_count} turns: {outcome}")
    for player_id, mission in record.missions.items():
        console.print(f"  {player_id}: {mission.description}")


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
    runner = TournamentRunner(
        participants=_named_factories(specs=models, settings=settings),
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


def _named_factories(specs: list[str], settings: Settings) -> dict[str, PlayerFactory]:
    factories = {}
    for spec in specs:
        base_name = spec.rsplit(":", maxsplit=1)[-1]
        name = base_name
        suffix = 2
        while name in factories:
            name = f"{base_name}#{suffix}"
            suffix += 1
        factories[name] = _player_factory(spec=spec, settings=settings)
    return factories


def _player_factory(spec: str, settings: Settings) -> PlayerFactory:
    if spec == "random":
        return lambda player_id, rng: RandomPlayer(player_id=player_id, rng=rng)
    agent = _build_agent(spec=spec, settings=settings)

    def create_llm_player(player_id: PlayerId, rng: random.Random) -> Player:
        return LLMPlayer(player_id=player_id, agent=agent, max_invalid_retries=settings.max_invalid_retries, rng=rng)

    return create_llm_player


def _build_agent(spec: str, settings: Settings) -> Agent:
    provider, _, model = spec.partition(":")
    if not model:
        raise typer.BadParameter(f"'{spec}' is not 'random' or a provider:model spec.")
    api_key = _require_api_key(provider=provider, settings=settings)
    match provider:
        case "anthropic":
            return AnthropicAgent(model=model, api_key=api_key, max_tokens=settings.max_agent_tokens)
        case "openai":
            return OpenAIAgent(model=model, api_key=api_key, max_tokens=settings.max_agent_tokens)
        case "google":
            return GeminiAgent(model=model, api_key=api_key, max_tokens=settings.max_agent_tokens)
        case "xai":
            return GrokAgent(model=model, api_key=api_key, max_tokens=settings.max_agent_tokens)
        case unknown_provider:
            raise typer.BadParameter(f"Unknown provider '{unknown_provider}'; use anthropic/openai/google/xai.")


def _require_api_key(provider: str, settings: Settings) -> str:
    keys_by_provider = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "google": settings.google_api_key,
        "xai": settings.xai_api_key,
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
