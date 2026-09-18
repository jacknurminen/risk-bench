"""Worker processes for games launched from the web app.

Each game runs in its own spawned daemon process calling the same
``risk_bench.launch`` functions as the CLI. Daemon children are tied to the
server's lifetime: shutting the server down terminates the games, which —
because the journal is flushed per line — merely pauses them; they can be
resumed later. Child stdout/stderr go to the game's ``.log`` file, which only
carries errors since workers run non-verbose: the journal is the record.
"""

import multiprocessing
import sys
from collections.abc import Callable
from multiprocessing.process import BaseProcess
from pathlib import Path

from risk_bench import launch
from risk_bench.config import load_settings
from risk_bench.players.human import mailbox_for
from risk_bench.players.llm import ProviderUnavailableError


class GameProcessManager:
    """Owns one worker process per running game: launch, resume, pause, liveness."""

    def __init__(self) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._processes: dict[str, BaseProcess] = {}
        # evaluation workers are tracked apart from game workers: a game may be evaluated while
        # it is itself paused, so the two must not collide on the same game id. Each worker is
        # kept with its panel specs so progress reporting knows how many calls to expect.
        self._evaluations: dict[str, tuple[BaseProcess, list[str]]] = {}

    def launch_game(
        self,
        game_id: str,
        players: list[str],
        seed: int,
        commentators: list[str],
        max_turns: int | None,
        journal_path: Path,
        log_path: Path,
    ) -> None:
        """Start a new game in its own worker process.

        Args:
            game_id: The game's identity within the manager.
            players: Player specs; their order does not fix seating, which is shuffled before play.
            seed: The game seed.
            commentators: The commentator panel as one model spec per member; empty for none.
            max_turns: Turn limit, or None for the settings default.
            journal_path: Where the worker writes the journal.
            log_path: Where the worker's output goes.
        """
        arguments = (str(log_path), players, seed, commentators, max_turns, str(journal_path))
        self._start(game_id=game_id, target=_run_new_game_in_worker, arguments=arguments)

    def resume_game(self, game_id: str, journal_path: Path, commentators: list[str], log_path: Path) -> None:
        """Continue a paused game in a new worker process.

        Args:
            game_id: The game's identity within the manager.
            journal_path: The unfinished journal to resume from.
            commentators: The commentator panel as one model spec per member; empty for none.
            log_path: Where the worker's output is appended.
        """
        (mailbox_for(journal_path) / "request.json").unlink(missing_ok=True)
        arguments = (str(log_path), str(journal_path), commentators)
        self._start(game_id=game_id, target=_resume_game_in_worker, arguments=arguments)

    def evaluate_game(self, game_id: str, journal_path: Path, evaluator_specs: list[str], log_path: Path) -> None:
        """Run a whole-game evaluator panel over a stored game in its own worker process.

        Off the game's critical path: the game need not be running (a paused or finished
        journal is the clean case), and the worker writes the ``game.eval.jsonl`` sidecar.

        Args:
            game_id: The game's identity within the manager.
            journal_path: The stored journal to evaluate.
            evaluator_specs: The evaluator panel as one model spec per member.
            log_path: Where the worker's output goes.
        """
        arguments = (str(log_path), str(journal_path), evaluator_specs)
        process = self._context.Process(target=_run_evaluation_in_worker, args=arguments, daemon=True)
        process.start()
        self._evaluations[game_id] = (process, list(evaluator_specs))

    def is_running(self, game_id: str) -> bool:
        """Tell whether the game's worker process is currently alive.

        Args:
            game_id: The game to check.

        Returns:
            True while the worker runs; exited workers are reaped on the way.
        """
        process = self._processes.get(game_id)
        if process is None:
            return False
        if process.is_alive():
            return True
        del self._processes[game_id]
        return False

    def is_evaluating(self, game_id: str) -> bool:
        """Tell whether an evaluation worker for the game is currently alive.

        Args:
            game_id: The game to check.

        Returns:
            True while an evaluation runs; exited workers are reaped on the way.
        """
        entry = self._evaluations.get(game_id)
        if entry is None:
            return False
        if entry[0].is_alive():
            return True
        del self._evaluations[game_id]
        return False

    def evaluating_panel(self, game_id: str) -> list[str] | None:
        """The panel specs of the game's running evaluation, in panel order.

        Args:
            game_id: The game to check.

        Returns:
            One model spec per member while an evaluation runs, else None.
        """
        if not self.is_evaluating(game_id=game_id):
            return None
        return self._evaluations[game_id][1]

    def pause_game(self, game_id: str) -> bool:
        """Terminate the game's worker; the per-line-flushed journal keeps it resumable.

        Args:
            game_id: The game to pause.

        Returns:
            True if a running worker was terminated, False if none was running.
        """
        process = self._processes.get(game_id)
        if process is None or not process.is_alive():
            return False
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        del self._processes[game_id]
        return True

    def shutdown(self) -> None:
        """Pause every running game; called when the server shuts down."""
        for game_id in list(self._processes):
            self.pause_game(game_id=game_id)

    def _start(self, game_id: str, target: Callable[..., object], arguments: tuple[object, ...]) -> None:
        process = self._context.Process(target=target, args=arguments, daemon=True)
        process.start()
        self._processes[game_id] = process


def _run_new_game_in_worker(
    log_path: str,
    players: list[str],
    seed: int,
    commentators: list[str],
    max_turns: int | None,
    journal_path: str,
) -> None:
    _redirect_output(log_path=log_path, append=False)
    try:
        launch.run_new_game(
            models=players,
            seed=seed,
            settings=load_settings(),
            commentators=commentators,
            journal_path=Path(journal_path),
            max_turns=max_turns,
            verbose=False,
            human_controls=True,
        )
    except ProviderUnavailableError as error:
        print(f"[paused] {error}")


def _resume_game_in_worker(log_path: str, journal_path: str, commentators: list[str]) -> None:
    _redirect_output(log_path=log_path, append=True)
    try:
        launch.resume_game(
            journal_file=Path(journal_path),
            settings=load_settings(),
            commentators=commentators,
            verbose=False,
            human_controls=True,
        )
    except ProviderUnavailableError as error:
        print(f"[paused] {error}")


def _run_evaluation_in_worker(log_path: str, journal_path: str, evaluator_specs: list[str]) -> None:
    _redirect_output(log_path=log_path, append=False)
    try:
        launch.run_evaluation(
            journal_path=Path(journal_path), evaluator_specs=evaluator_specs, settings=load_settings()
        )
    except ProviderUnavailableError as error:
        print(f"[evaluation-aborted] {error}")


def _redirect_output(log_path: str, append: bool) -> None:
    stream = open(log_path, "a" if append else "w", buffering=1, encoding="utf-8")  # noqa: SIM115 - lives until exit
    sys.stdout = stream
    sys.stderr = stream
