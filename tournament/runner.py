from __future__ import annotations

import random
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
        n_games: int,
        verbose: bool,
    ) -> None:
        self.agent_configs = agent_configs
        self.n_games = n_games
        self.verbose = verbose

    def run(self) -> list[GameRecord]:
        """Run n_games games and return all game records.

        Returns:
            List of GameRecord objects, one per game played.
        """
        records: list[GameRecord] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Running {self.n_games} game(s)...", total=self.n_games)

            for game_number in range(1, self.n_games + 1):
                progress.update(task, description=f"Game {game_number}/{self.n_games}")
                record = self._run_single_game(game_number=game_number)
                records.append(record)
                self._record_result(record=record)
                progress.advance(task)

        store.rebuild_leaderboard()
        console.print(f"\n[green]Tournament complete! {self.n_games} game(s) played.[/green]")
        return records

    def _run_single_game(self, game_number: int) -> GameRecord:
        # Shuffle player order each game for fairness
        configs = list(self.agent_configs)
        random.shuffle(configs)

        colors = ["red", "blue", "green", "yellow", "purple", "orange"]
        player_colors = colors[: len(configs)]
        color_to_agent: dict[str, Any] = {}

        for agent_config, color in zip(configs, player_colors):
            agent = agent_config["agent"]
            agent.color = color
            color_to_agent[color] = agent

        started_at = datetime.utcnow()
        engine = GameEngine(agents=color_to_agent, verbose=self.verbose)
        engine.new_game(players=player_colors)
        final_state = engine.run()
        finished_at = datetime.utcnow()

        winner_color = final_state.winner
        winner_agent_id: str | None = None
        if winner_color:
            for agent_config, color in zip(configs, player_colors):
                if color == winner_color:
                    winner_agent_id = agent_config["id"]
                    break

        record = GameRecord(
            id=final_state.game_id,
            agents=[agent_config["id"] for agent_config in configs],
            colors=player_colors,
            started_at=started_at,
            finished_at=finished_at,
            winner_color=winner_color,
            winner_agent_id=winner_agent_id,
            turn_count=final_state.turn,
            win_reason=final_state.win_reason,
            chat_log=[
                ChatRecord(
                    turn=entry.turn,
                    event=entry.event,
                    **{"from": entry.from_color},
                    text=entry.text,
                )
                for entry in final_state.chat_log
            ],
        )

        store.save_game(game=record)
        return record

    def _record_result(self, record: GameRecord) -> None:
        agents = {
            agent_config["id"]: store.get_agent(agent_id=agent_config["id"]) for agent_config in self.agent_configs
        }

        # Ensure all agents are registered
        for agent_config in self.agent_configs:
            if agents[agent_config["id"]] is None:
                agents[agent_config["id"]] = AgentRecord(
                    id=agent_config["id"],
                    name=agent_config["name"],
                    model=agent_config["model"],
                    provider=agent_config["provider"],
                )

        ratings = {agent_id: agent_record.elo for agent_id, agent_record in agents.items() if agent_record is not None}

        if record.winner_agent_id and record.winner_agent_id in ratings:
            new_ratings = update_elo(ratings=ratings, winner=record.winner_agent_id)
        else:
            new_ratings = ratings  # no winner (shouldn't happen)

        for agent_config in self.agent_configs:
            agent_id = agent_config["id"]
            agent_record = agents[agent_id]
            if agent_record is None:
                continue
            agent_record.elo = round(new_ratings.get(agent_id, agent_record.elo), 2)
            agent_record.games_played += 1
            if agent_id == record.winner_agent_id:
                agent_record.wins += 1
            else:
                agent_record.losses += 1
            store.upsert_agent(agent=agent_record)

        if self.verbose:
            console.print(f"  Winner: {record.winner_color} ({record.winner_agent_id}) — {record.win_reason}")
