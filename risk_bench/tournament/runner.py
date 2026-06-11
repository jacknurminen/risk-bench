import itertools
import random
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from risk_bench.game.types import PlayerId
from risk_bench.players.base import Player
from risk_bench.runner.game_runner import GameRecord, GameRunner
from risk_bench.runner.journal import SCHEMA_VERSION, JournalHeader, JournalWriter
from risk_bench.tournament.elo import INITIAL_RATING, update_ratings
from risk_bench.tournament.results import AgentStanding, GameResult, TournamentReport

type PlayerFactory = Callable[[PlayerId, random.Random], Player]
"""Creates a fresh player for one game; takes the seat's player id and a seeded rng."""


class TournamentRunner:
    """Schedules and runs many games between agents, maintaining Elo ratings.

    Participants are player factories rather than live players, so every game
    gets fresh player instances (per-game invalid-action counts, no state
    leakage) and random baselines mix freely with LLM agents. Matchups cycle
    through every combination of participants and rotate seat order across
    games because turn order matters in Risk; per-game seeds are derived from
    ``base_seed`` so an entire tournament is reproducible.
    """

    def __init__(
        self,
        participants: dict[str, PlayerFactory],
        participant_specs: dict[str, str],
        games: int,
        players_per_game: int,
        base_seed: int,
        max_turns: int,
        allow_private_messages: bool,
        max_negotiation_rounds: int,
        journal_directory: Path | None,
        k_factor: float,
        verbose: bool,
    ) -> None:
        if not 3 <= players_per_game <= 6:
            raise ValueError(f"Risk seats 3-6 players per game, got {players_per_game}.")
        if players_per_game > len(participants):
            raise ValueError(
                f"Cannot seat {players_per_game} players per game with only {len(participants)} participants."
            )
        self._participants = participants
        self._participant_specs = participant_specs
        self._journal_directory = journal_directory
        self._games = games
        self._players_per_game = players_per_game
        self._base_seed = base_seed
        self._max_turns = max_turns
        self._allow_private_messages = allow_private_messages
        self._max_negotiation_rounds = max_negotiation_rounds
        self._k_factor = k_factor
        self._verbose = verbose

    def run(self) -> TournamentReport:
        """Run all scheduled games, updating ratings after each.

        Returns:
            The tournament report with final standings and per-game results.
        """
        ratings = {name: INITIAL_RATING for name in self._participants}
        wins: Counter[str] = Counter()
        draws: Counter[str] = Counter()
        games_played: Counter[str] = Counter()
        invalid_actions: Counter[str] = Counter()
        results = []

        for game_index, seating in enumerate(self._seatings()):
            record, journal_path = self._run_one_game(game_index=game_index, seating=seating)
            results.append(_summarize(record=record, journal_path=journal_path))

            game_ratings = {name: ratings[name] for name in seating}
            ratings |= update_ratings(ratings=game_ratings, winner=record.winner, k_factor=self._k_factor)
            for name in seating:
                games_played[name] += 1
                invalid_actions[name] += record.invalid_action_counts[name]
                if record.winner is None:
                    draws[name] += 1
            if record.winner is not None:
                wins[record.winner] += 1
            if self._verbose:
                outcome = record.winner or "draw"
                print(f"Game {game_index + 1}/{self._games} (seed {record.seed}): {outcome}")

        return TournamentReport(
            created_at=datetime.now(tz=UTC),
            games=self._games,
            players_per_game=self._players_per_game,
            k_factor=self._k_factor,
            base_seed=self._base_seed,
            max_turns=self._max_turns,
            allow_private_messages=self._allow_private_messages,
            max_negotiation_rounds=self._max_negotiation_rounds,
            standings=_standings(
                ratings=ratings, games_played=games_played, wins=wins, draws=draws, invalid_actions=invalid_actions
            ),
            results=results,
        )

    def _seatings(self) -> list[tuple[str, ...]]:
        matchups = list(itertools.combinations(sorted(self._participants), self._players_per_game))
        seatings = []
        for game_index in range(self._games):
            matchup = matchups[game_index % len(matchups)]
            rotation = (game_index // len(matchups)) % self._players_per_game
            seatings.append(matchup[rotation:] + matchup[:rotation])
        return seatings

    def _run_one_game(self, game_index: int, seating: tuple[str, ...]) -> tuple[GameRecord, Path | None]:
        seed = self._base_seed + game_index
        players = {name: self._participants[name](name, random.Random(f"{seed}:{name}")) for name in seating}
        journal = self._open_journal(game_index=game_index, seed=seed, seating=seating)
        runner = GameRunner(
            players=players,
            seed=seed,
            max_turns=self._max_turns,
            allow_private_messages=self._allow_private_messages,
            max_negotiation_rounds=self._max_negotiation_rounds,
            journal=journal,
            verbose=self._verbose,
        )
        try:
            return runner.run(), journal.path if journal is not None else None
        finally:
            if journal is not None:
                journal.close()

    def _open_journal(self, game_index: int, seed: int, seating: tuple[str, ...]) -> JournalWriter | None:
        if self._journal_directory is None:
            return None
        started_at = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
        path = self._journal_directory / f"{started_at}-game{game_index + 1}-seed{seed}.jsonl"
        journal = JournalWriter(path=path, append=False)
        journal.write_header(
            header=JournalHeader(
                schema_version=SCHEMA_VERSION,
                seed=seed,
                player_specs={name: self._participant_specs[name] for name in seating},
                max_turns=self._max_turns,
                allow_private_messages=self._allow_private_messages,
                max_negotiation_rounds=self._max_negotiation_rounds,
            )
        )
        return journal


def _summarize(record: GameRecord, journal_path: Path | None) -> GameResult:
    chat_message_counts = Counter(message.sender for message in record.chat)
    return GameResult(
        seed=record.seed,
        player_names=list(record.player_ids),
        missions={player_id: mission.description for player_id, mission in record.missions.items()},
        winner=record.winner,
        turn_count=record.turn_count,
        invalid_action_counts=dict(record.invalid_action_counts),
        chat_message_counts={player_id: chat_message_counts[player_id] for player_id in record.player_ids},
        journal_path=str(journal_path) if journal_path is not None else None,
    )


def _standings(
    ratings: dict[str, float],
    games_played: Counter[str],
    wins: Counter[str],
    draws: Counter[str],
    invalid_actions: Counter[str],
) -> list[AgentStanding]:
    standings = [
        AgentStanding(
            name=name,
            rating=rating,
            games_played=games_played[name],
            wins=wins[name],
            draws=draws[name],
            invalid_actions=invalid_actions[name],
        )
        for name, rating in ratings.items()
    ]
    return sorted(standings, key=lambda standing: standing.rating, reverse=True)
