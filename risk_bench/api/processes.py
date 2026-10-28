"""Worker processes for games launched from the web app.

Each game runs in its own spawned daemon process calling the same
``risk_bench.launch`` functions as the CLI. Daemon children are tied to the
server's lifetime: shutting the server down terminates the games, which —
because the journal is flushed per line — merely pauses them; they can be
resumed later. Child stdout/stderr go to the game's ``.log`` file, which only
carries errors since workers run non-verbose: the journal is the record.
"""

import json
import multiprocessing
import sys
import threading
from collections.abc import Callable
from functools import wraps
from multiprocessing.process import BaseProcess
from pathlib import Path

from risk_bench import launch
from risk_bench.config import load_settings, settings_for_new_game
from risk_bench.players.human import mailbox_for
from risk_bench.players.llm import ProviderUnavailableError
from risk_bench.players.profiles import ExperimentPlayer
from risk_bench.runner import gaslighting_evaluation
from risk_bench.runner.tournament_evaluation import interrupt_batch, list_batches, run_batch


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class GameProcessManager:
    """Owns game workers and advances tournament queues within their concurrency limits."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._pending: dict[str, tuple[Callable[..., object], tuple[object, ...]]] = {}
        self._tournament_for: dict[str, str] = {}
        self._limits: dict[str, int] = {}
        self._context = multiprocessing.get_context("spawn")
        self._processes: dict[str, BaseProcess] = {}
        # evaluation workers are tracked apart from game workers: a game may be evaluated while
        # it is itself paused, so the two must not collide on the same game id. Each worker is
        # kept with its panel specs so progress reporting knows how many calls to expect.
        self._evaluations: dict[str, tuple[BaseProcess, list[str], bool]] = {}
        self._evaluation_batches: dict[Path, tuple[BaseProcess, dict]] = {}
        self._gaslighting_judges: dict[str, BaseProcess] = {}

    @_locked
    def evaluate_tournament(self, path: Path) -> None:
        batch = json.loads(path.read_text())
        process = self._context.Process(target=_run_tournament_evaluation_in_worker, args=(str(path),), daemon=True)
        try:
            process.start()
        except Exception:
            interrupt_batch(path)
            raise
        self._evaluation_batches[path] = (process, batch)

    @_locked
    def reconcile_tournament_evaluations(self, directory: Path) -> None:
        self._reap_batches()
        active = {path.stem for path in self._evaluation_batches if path.parent.parent == directory}
        for batch in list_batches(directory):
            pending = any(j["status"] in {"queued", "running"} for j in batch["games"].values())
            if batch["id"] not in active and pending:
                interrupt_batch(directory / "evaluation-batches" / f"{batch['id']}.json")

    def _reap_batches(self) -> None:
        for path, (process, _) in list(self._evaluation_batches.items()):
            if not process.is_alive():
                process.join(timeout=0)
                interrupt_batch(path)
                del self._evaluation_batches[path]

    def _batch_for(self, game_id: str) -> dict | None:
        self._reap_batches()
        return next((batch for _, batch in self._evaluation_batches.values() if game_id in batch["games"]), None)

    def launch_game(
        self,
        game_id: str,
        players: list[str],
        seed: int,
        commentators: list[str],
        max_turns: int | None,
        additional_context: list[str],
        journal_path: Path,
        log_path: Path,
        seat_order: list[int] | None = None,
        experiment_player: ExperimentPlayer | None = None,
    ) -> None:
        """Start a game, or queue it until its tournament has a free worker slot.

        Args:
            game_id: The game's identity within the manager.
            players: Player specs in roster order.
            seed: The game seed.
            commentators: The commentator panel as one model spec per member; empty for none.
            max_turns: Turn limit, or None for the settings default.
            additional_context: Extra information per player from the first turn on, aligned with
                ``players``; empty strings give none.
            journal_path: Where the worker writes the journal.
            log_path: Where the worker's output goes.
            seat_order: Exact 1-based roster positions in seat order; None uses seeded shuffling.
            experiment_player: The selected roster player and frozen experiment prompt, if enabled.
        """
        arguments = (str(log_path), players, seed, commentators, max_turns, additional_context,
                     str(journal_path), experiment_player, seat_order)
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

    @_locked
    def evaluate_game(self, game_id: str, journal_path: Path, evaluator_specs: list[str], log_path: Path,
                      live: bool = False, restart: bool = False) -> None:
        """Run an evaluator panel over a stored game in its own worker process.

        Off the game's critical path: the game need not be running (a paused or finished
        journal is the clean case), and the worker writes the ``game.eval.jsonl`` sidecar.

        Args:
            game_id: The game's identity within the manager.
            journal_path: The stored journal to evaluate.
            evaluator_specs: The evaluator panel as one model spec per member.
            log_path: Where the worker's output goes.
        """
        arguments = (str(log_path), str(journal_path), evaluator_specs, live, restart)
        process = self._context.Process(target=_run_evaluation_in_worker, args=arguments, daemon=True)
        process.start()
        self._evaluations[game_id] = (process, list(evaluator_specs), live)

    @_locked
    def judge_gaslighting(self, game_id: str, journal_path: Path, evaluator_spec: str) -> None:
        """Run the single-call gaslighting judge over a stored game in its own worker process.

        It is tracked apart from the panel evaluation, so the two can run side by side.

        Args:
            game_id: The game's identity within the manager.
            journal_path: The stored journal of a game with a Gaslighter player.
            evaluator_spec: The judge's model spec.
        """
        arguments = (str(journal_path), evaluator_spec)
        process = self._context.Process(target=_run_gaslighting_judge_in_worker, args=arguments, daemon=True)
        process.start()
        self._gaslighting_judges[game_id] = process

    @_locked
    def is_judging_gaslighting(self, game_id: str) -> bool:
        """Tell whether a gaslighting judge worker for the game is currently alive.

        Args:
            game_id: The game to check.

        Returns:
            True while the judge runs; an exited worker is reaped on the way.
        """
        process = self._gaslighting_judges.get(game_id)
        if process is None:
            return False
        if process.is_alive():
            return True
        process.join(timeout=0)
        del self._gaslighting_judges[game_id]
        return False

    @_locked
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
        process.join(timeout=0)
        del self._processes[game_id]
        return False

    @_locked
    def is_evaluating(self, game_id: str) -> bool:
        """Tell whether an evaluation worker for the game is currently alive.

        Args:
            game_id: The game to check.

        Returns:
            True while an evaluation runs; exited workers are reaped on the way.
        """
        if self._batch_for(game_id):
            return True
        entry = self._evaluations.get(game_id)
        if entry is None:
            return False
        if entry[0].is_alive():
            return True
        del self._evaluations[game_id]
        return False

    @_locked
    def evaluating_panel(self, game_id: str) -> list[str] | None:
        """The panel specs of the game's running evaluation, in panel order.

        Args:
            game_id: The game to check.

        Returns:
            One model spec per member while an evaluation runs, else None.
        """
        batch = self._batch_for(game_id)
        if batch:
            return batch["panel"]
        if not self.is_evaluating(game_id=game_id):
            return None
        return self._evaluations[game_id][1]

    @_locked
    def evaluating_live(self, game_id: str) -> bool:
        """Whether the detached evaluation follows completed rounds."""
        batch = self._batch_for(game_id)
        if batch:
            return batch["method"] == "live_rounds"
        return self.is_evaluating(game_id) and bool(self._evaluations.get(game_id, (None, None, False))[2])

    @_locked
    def pause_game(self, game_id: str) -> bool:
        """Terminate the game's worker; the per-line-flushed journal keeps it resumable.

        Args:
            game_id: The game to pause.

        Returns:
            True if a running worker was terminated, False if none was running.
        """
        if self._pending.pop(game_id, None) is not None:
            return True
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

    def pause_evaluation(self, game_id: str) -> bool:
        """Stop a live panel; its last atomic round snapshot remains resumable."""
        entry = self._evaluations.get(game_id)
        if entry is None or not entry[0].is_alive():
            return False
        process = entry[0]
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        del self._evaluations[game_id]
        return True

    def shutdown(self) -> None:
        """Pause every running game; called when the server shuts down."""
        with self._lock:
            self._stop.set()
            self._pending.clear()
        if self._scheduler is not None:
            self._scheduler.join(timeout=2)
        for game_id in list(self._processes):
            self.pause_game(game_id=game_id)
        for game_id in list(self._evaluations):
            self.pause_evaluation(game_id=game_id)
        for process in self._gaslighting_judges.values():
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        self._gaslighting_judges.clear()
        for path, (process, _) in list(self._evaluation_batches.items()):
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
            interrupt_batch(path)
        self._evaluation_batches.clear()

    @_locked
    def register_tournament(self, tournament_id: str, game_ids: list[str], parallel_games: int) -> None:
        if parallel_games < 1:
            raise ValueError("Parallel games must be at least one.")
        self._limits[tournament_id] = parallel_games
        self._tournament_for.update(dict.fromkeys(game_ids, tournament_id))

    @_locked
    def is_queued(self, game_id: str) -> bool:
        return game_id in self._pending

    @_locked
    def _start(self, game_id: str, target: Callable[..., object], arguments: tuple[object, ...]) -> None:
        if self._stop.is_set():
            raise RuntimeError("The game server is shutting down.")
        if self.is_running(game_id) or self.is_queued(game_id):
            raise ValueError("The game is already running or queued.")
        self._pending[game_id] = (target, arguments)
        self._drain_queue()
        if self._pending and self._scheduler is None:
            self._scheduler = threading.Thread(target=self._schedule, name="tournament-queue", daemon=True)
            self._scheduler.start()

    def _schedule(self) -> None:
        while not self._stop.wait(0.2):
            self._drain_queue()

    @_locked
    def _drain_queue(self) -> None:
        if self._stop.is_set():
            return
        for game_id in list(self._pending):
            tournament = self._tournament_for.get(game_id)
            if tournament is not None:
                active = sum(self.is_running(other) for other in list(self._processes)
                             if self._tournament_for.get(other) == tournament)
                if active >= self._limits[tournament]:
                    continue
            target, arguments = self._pending.pop(game_id)
            process = self._context.Process(target=target, args=arguments, daemon=True)
            try:
                process.start()
            except Exception as error:
                # A failed spawn must not strand the rest of the queue.
                Path(str(arguments[0])).write_text(f"[startup-error] {error}\n", encoding="utf-8")
                continue
            self._processes[game_id] = process


def _run_new_game_in_worker(
    log_path: str,
    players: list[str],
    seed: int,
    commentators: list[str],
    max_turns: int | None,
    additional_context: list[str],
    journal_path: str,
    experiment_player: ExperimentPlayer | None = None,
    seat_order: list[int] | None = None,
) -> None:
    _redirect_output(log_path=log_path, append=False)
    try:
        launch.run_new_game(
            models=players,
            seed=seed,
            settings=settings_for_new_game(Path(journal_path)),
            commentators=commentators,
            journal_path=Path(journal_path),
            max_turns=max_turns,
            verbose=False,
            additional_context=additional_context,
            human_controls=True,
            seat_order=seat_order,
            experiment_player=experiment_player,
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


def _run_tournament_evaluation_in_worker(path: str) -> None:
    batch_path = Path(path)
    _redirect_output(str(batch_path.with_suffix(".log")), append=False)
    try:
        run_batch(batch_path, load_settings())
    finally:
        interrupt_batch(batch_path)


def _run_evaluation_in_worker(
    log_path: str, journal_path: str, evaluator_specs: list[str], live: bool = False, restart: bool = False
) -> None:
    _redirect_output(log_path=log_path, append=live)
    error_path = Path(journal_path).with_suffix(".eval.error.json")
    error_path.unlink(missing_ok=True)
    try:
        if live:
            from risk_bench.runner.live_evaluation import run_live_evaluation

            # Standalone reviews catch up and exit; the game worker owns future round boundaries.
            run_live_evaluation(Path(journal_path), evaluator_specs, load_settings(), follow=False, restart=restart)
        else:
            launch.run_evaluation(
                journal_path=Path(journal_path), evaluator_specs=evaluator_specs, settings=load_settings()
            )
    except ProviderUnavailableError as error:
        print(f"[evaluation-aborted] {error}")
        error_path.write_text(json.dumps({"detail": str(error)}), encoding="utf-8")
    except Exception as error:
        print(f"[evaluation-aborted] {type(error).__name__}: {error}")
        error_path.write_text(json.dumps({"detail": str(error)}), encoding="utf-8")


def _run_gaslighting_judge_in_worker(journal_path: str, evaluator_spec: str) -> None:
    path = Path(journal_path)
    _redirect_output(log_path=str(path.parent / "gaslighting.log"), append=False)
    error_path = gaslighting_evaluation.gaslighting_error_path_for_journal(journal_path=path)
    error_path.unlink(missing_ok=True)
    try:
        launch.run_gaslighting_evaluation(journal_path=path, evaluator_spec=evaluator_spec, settings=load_settings())
    except Exception as error:
        print(f"[gaslighting-aborted] {type(error).__name__}: {error}")
        error_path.write_text(json.dumps({"detail": str(error)}), encoding="utf-8")


def _redirect_output(log_path: str, append: bool) -> None:
    stream = open(log_path, "a" if append else "w", buffering=1, encoding="utf-8")  # noqa: SIM115 - lives until exit
    sys.stdout = stream
    sys.stderr = stream
