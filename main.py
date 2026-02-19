"""CLI entrypoint for Risk-Bench."""

from __future__ import annotations

import uuid
from typing import Annotated

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

import config
import storage.store as store
from storage.models import AgentRecord

app = typer.Typer(help="Risk-Bench: LLM benchmark via the game of Risk", no_args_is_help=True)
console = Console()

# ---------------------------------------------------------------------------
# tournament subcommand
# ---------------------------------------------------------------------------
tournament_app = typer.Typer(help="Tournament management")
app.add_typer(tournament_app, name="tournament")


@tournament_app.command("run")
def run_tournament(
    agents: Annotated[str, typer.Option("--agents", help="Comma-separated agent IDs (must be registered)")] = "",
    games: Annotated[int, typer.Option("--games", help="Number of games to play")] = 1,
    verbose: Annotated[bool, typer.Option("--verbose/--quiet")] = True,
) -> None:
    """Run a round-robin tournament between registered agents."""
    from tournament.runner import TournamentRunner

    agent_ids = [a.strip() for a in agents.split(",") if a.strip()]
    if len(agent_ids) < 2:
        console.print("[red]Need at least 2 agents to run a tournament.[/red]")
        raise typer.Exit(1)

    registered = store.load_agents()
    reg_map = {a.id: a for a in registered}

    agent_configs = []
    for aid in agent_ids:
        if aid not in reg_map:
            console.print(f"[red]Agent {aid!r} not found in registry. Use 'agent add' first.[/red]")
            raise typer.Exit(1)
        rec = reg_map[aid]
        agent_instance = _build_agent(rec, color="red")  # color assigned per game
        agent_configs.append(
            {
                "id": rec.id,
                "name": rec.name,
                "model": rec.model,
                "provider": rec.provider,
                "agent": agent_instance,
            }
        )

    runner = TournamentRunner(agent_configs=agent_configs, n_games=games, verbose=verbose)
    records = runner.run()

    console.print(f"\n[bold]Results: {len(records)} game(s) completed[/bold]")
    lb = store.rebuild_leaderboard()
    _print_leaderboard(lb)


@tournament_app.command("quick")
def quick_game(
    n_players: Annotated[int, typer.Option("--players", help="Number of random agents")] = 3,
    verbose: Annotated[bool, typer.Option("--verbose/--quiet")] = True,
) -> None:
    """Run a quick test game with random agents."""
    from agents.random import RandomAgent
    from game.engine import GameEngine

    colors = ["red", "blue", "green", "yellow", "purple", "orange"][:n_players]
    agents = {color: RandomAgent(color=color, name=f"random-{color}") for color in colors}

    console.print(f"[yellow]Running quick game with {n_players} random agents...[/yellow]")
    engine = GameEngine(agents=agents, verbose=verbose)
    engine.new_game(players=colors)
    final = engine.run()

    console.print("\n[bold green]Game over![/bold green]")
    console.print(f"Winner: [bold]{final.winner}[/bold] — {final.win_reason}")
    console.print(f"Turns: {final.turn}")
    console.print(f"Chat messages: {len(final.chat_log)}")


# ---------------------------------------------------------------------------
# agent subcommand
# ---------------------------------------------------------------------------
agent_app = typer.Typer(help="Agent registry management")
app.add_typer(agent_app, name="agent")


@agent_app.command("add")
def agent_add(
    name: Annotated[str, typer.Option("--name")] = "",
    provider: Annotated[str, typer.Option("--provider", help="anthropic|openai|xai|google|random")] = "",
    model: Annotated[str, typer.Option("--model", help="Model name (uses provider default if omitted)")] = "",
    agent_id: Annotated[str, typer.Option("--id", help="Custom ID (auto-generated if omitted)")] = "",
) -> None:
    """Register a new agent."""
    if not name:
        console.print("[red]--name is required[/red]")
        raise typer.Exit(1)
    if not provider:
        console.print("[red]--provider is required[/red]")
        raise typer.Exit(1)

    if not model:
        model = config.DEFAULT_MODELS.get(provider, "unknown")
    if not agent_id:
        agent_id = f"{provider}-{name.lower().replace(' ', '-')}-{str(uuid.uuid4())[:6]}"

    rec = AgentRecord(id=agent_id, name=name, model=model, provider=provider)
    store.upsert_agent(rec)
    console.print(f"[green]Registered agent:[/green] {name} (id: {agent_id})")


@agent_app.command("list")
def agent_list() -> None:
    """List all registered agents."""
    agents = store.load_agents()
    if not agents:
        console.print("No agents registered.")
        return

    table = Table(title="Registered Agents")
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Elo", justify="right")
    table.add_column("W/L")

    for a in sorted(agents, key=lambda x: x.elo, reverse=True):
        table.add_row(a.id[:16], a.name, a.provider, a.model, f"{a.elo:.0f}", f"{a.wins}/{a.losses}")

    console.print(table)


@agent_app.command("remove")
def agent_remove(
    agent_id: Annotated[str, typer.Argument(help="Agent ID to remove")],
) -> None:
    """Remove an agent from the registry."""
    agents = store.load_agents()
    before = len(agents)
    agents = [a for a in agents if a.id != agent_id]
    if len(agents) == before:
        console.print(f"[red]Agent {agent_id!r} not found.[/red]")
        raise typer.Exit(1)
    store.save_agents(agents)
    console.print(f"[green]Removed agent {agent_id}[/green]")


# ---------------------------------------------------------------------------
# serve subcommand
# ---------------------------------------------------------------------------
@app.command()
def serve(
    host: Annotated[str, typer.Option("--host")] = config.DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port")] = config.DEFAULT_PORT,
    reload: Annotated[bool, typer.Option("--reload/--no-reload")] = False,
) -> None:
    """Start the web dashboard."""
    console.print(f"[yellow]Starting Risk-Bench dashboard at http://{host}:{port}[/yellow]")
    uvicorn.run("web.app:app", host=host, port=port, reload=reload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_agent(rec: AgentRecord, color: str) -> object:
    """Instantiate the correct agent class from a registry record."""
    from agents.random import RandomAgent

    if rec.provider == "anthropic":
        from agents.anthropic import AnthropicAgent

        return AnthropicAgent(color=color, name=rec.name, model=rec.model, api_key=config.ANTHROPIC_API_KEY)
    if rec.provider == "openai":
        from agents.openai import OpenAIAgent

        return OpenAIAgent(color=color, name=rec.name, model=rec.model, api_key=config.OPENAI_API_KEY)
    if rec.provider == "xai":
        from agents.xai import GrokAgent

        return GrokAgent(color=color, name=rec.name, model=rec.model, api_key=config.XAI_API_KEY)
    if rec.provider == "google":
        from agents.google import GeminiAgent

        return GeminiAgent(color=color, name=rec.name, model=rec.model, api_key=config.GOOGLE_API_KEY)
    # default: random
    return RandomAgent(color=color, name=rec.name)


def _print_leaderboard(lb: object) -> None:
    from storage.models import Leaderboard

    assert isinstance(lb, Leaderboard)
    table = Table(title="Leaderboard")
    table.add_column("#", justify="right")
    table.add_column("Agent")
    table.add_column("Elo", justify="right", style="green")
    table.add_column("W", justify="right")
    table.add_column("L", justify="right")
    table.add_column("Win%", justify="right")

    for e in lb.entries:
        table.add_row(str(e.rank), e.name, f"{e.elo:.0f}", str(e.wins), str(e.losses), f"{e.win_rate:.1%}")

    console.print(table)


if __name__ == "__main__":
    app()
