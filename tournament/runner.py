"""Tournament orchestration — runs multiple games and records results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from game.engine import GameEngine
from storage import store
from storage.models import AgentRecord, ChatRecord, GameRecord
from tournament.elo import update_elo

console = Console()


class TournamentRunner:
    def __init__(
        self,
        agent_configs: list[dict[str, Any]],
        # each dict: {id, name, model, provider, agent: BaseAgent instance}
        n_games: int = 1,
        verbose: bool = True,
    ) -> None:
        self.agent_configs = agent_configs
        self.n_games = n_games
        self.verbose = verbose

    def run(self) -> list[GameRecord]:
        """Run n_games games and return all game records."""
        records: list[GameRecord] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Running {self.n_games} game(s)...", total=self.n_games)

            for game_num in range(1, self.n_games + 1):
                progress.update(task, description=f"Game {game_num}/{self.n_games}")
                record = self._run_single_game(game_num)
                records.append(record)
                self._record_result(record)
                progress.advance(task)

        store.rebuild_leaderboard()
        console.print(f"\n[green]Tournament complete! {self.n_games} game(s) played.[/green]")
        return records

    def _run_single_game(self, game_num: int) -> GameRecord:
        import random

        # Shuffle player order each game for fairness
        configs = list(self.agent_configs)
        random.shuffle(configs)

        # Build color assignments
        colors = ["red", "blue", "green", "yellow", "purple", "orange"]
        player_colors = colors[: len(configs)]
        color_to_agent: dict[str, Any] = {}

        for cfg, color in zip(configs, player_colors):
            agent = cfg["agent"]
            agent.color = color
            color_to_agent[color] = agent

        started_at = datetime.utcnow()
        engine = GameEngine(agents=color_to_agent, verbose=self.verbose)
        engine.new_game(players=player_colors)
        final_state = engine.run()
        finished_at = datetime.utcnow()

        # Map winner color -> agent id
        winner_color = final_state.winner
        winner_agent_id: str | None = None
        if winner_color:
            for cfg, color in zip(configs, player_colors):
                if color == winner_color:
                    winner_agent_id = cfg["id"]
                    break

        record = GameRecord(
            id=final_state.game_id,
            agents=[cfg["id"] for cfg in configs],
            colors=player_colors,
            started_at=started_at,
            finished_at=finished_at,
            winner_color=winner_color,
            winner_agent_id=winner_agent_id,
            turn_count=final_state.turn,
            win_reason=final_state.win_reason,
            chat_log=[
                ChatRecord(
                    turn=e.turn,
                    event=e.event,
                    **{"from": e.from_color},
                    text=e.text,
                )
                for e in final_state.chat_log
            ],
        )

        store.save_game(record)
        return record

    def _record_result(self, record: GameRecord) -> None:
        """Update agent Elo and win/loss records."""
        agents = {cfg["id"]: store.get_agent(cfg["id"]) for cfg in self.agent_configs}

        # Ensure all agents are registered
        for cfg in self.agent_configs:
            if agents[cfg["id"]] is None:
                agents[cfg["id"]] = AgentRecord(
                    id=cfg["id"],
                    name=cfg["name"],
                    model=cfg["model"],
                    provider=cfg["provider"],
                )

        # Build ratings dict
        ratings = {aid: ag.elo for aid, ag in agents.items() if ag is not None}

        if record.winner_agent_id and record.winner_agent_id in ratings:
            new_ratings = update_elo(ratings, record.winner_agent_id)
        else:
            new_ratings = ratings  # no winner (shouldn't happen)

        # Update records
        for cfg in self.agent_configs:
            aid = cfg["id"]
            ag = agents[aid]
            if ag is None:
                continue
            ag.elo = round(new_ratings.get(aid, ag.elo), 2)
            ag.games_played += 1
            if aid == record.winner_agent_id:
                ag.wins += 1
            else:
                ag.losses += 1
            store.upsert_agent(ag)

        if self.verbose:
            console.print(f"  Winner: {record.winner_color} ({record.winner_agent_id}) — {record.win_reason}")
