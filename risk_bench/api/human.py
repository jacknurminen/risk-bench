"""Human decision mailbox routes used by the existing reader’s turn card."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from risk_bench.api.processes import GameProcessManager
from risk_bench.chat.diplomacy import MessageVisibility
from risk_bench.game.board import BOARD
from risk_bench.game.engine import IllegalActionError
from risk_bench.game.types import Territory
from risk_bench.players.human import HumanResponse, mailbox_for, read_prompt, submit_response, view_to_json
from risk_bench.players.view import build_player_view
from risk_bench.runner.journal import RestoredGame, restore_game


def _restore(path: Path) -> RestoredGame:
    try:
        game = restore_game(path)
    except (ValueError, IllegalActionError) as error:
        raise HTTPException(status_code=409, detail=f"Cannot replay this game: {error}") from None
    if "human" not in game.header.player_specs.values():
        raise HTTPException(status_code=409, detail="This game has no human seat.")
    return game


def install_human_routes(app: FastAPI, manager: GameProcessManager, journal_path: Callable[[str], Path]) -> None:
    @app.get("/games/{game_id}/play", include_in_schema=False)
    def play_page(game_id: str) -> RedirectResponse:
        _restore(journal_path(game_id))
        return RedirectResponse(url=f"/games/{game_id}")

    @app.get("/api/games/{game_id}/human")
    def human_state(game_id: str) -> dict[str, Any]:
        path = journal_path(game_id)
        game = _restore(path)
        human_id = next(name for name, spec in game.header.player_specs.items() if spec == "human")
        running = manager.is_running(game_id)
        prompt = read_prompt(mailbox_for(path))
        waiting = bool(running and not game.is_finished and prompt and prompt["waiting"])
        if waiting and prompt is not None:
            # The exact decision view also includes this turn's filtered event history.
            view = prompt["view"]
        else:
            view = view_to_json(
                build_player_view(
                    state=game.state,
                    player_id=human_id,
                    visible_chat=tuple(
                        message
                        for message in game.messages
                        if message.turn_number == game.state.turn_number
                        and (
                            message.visibility is MessageVisibility.PUBLIC
                            or human_id in (message.sender, message.recipient)
                        )
                    ),
                    recent_events=(),
                    private_messages_allowed=game.header.allow_private_messages,
                    notes=None,
                )
            )
        return {
            "view": view,
            "running": running,
            "finished": game.is_finished,
            "winner": game.state.winner,
            "request": prompt if waiting else None,
            "continents": {str(t): str(BOARD.continent_of(t)) for t in Territory},
            "neighbors": {str(t): sorted(BOARD.neighbors_of(t)) for t in Territory},
        }

    @app.post("/api/games/{game_id}/human", status_code=202)
    def human_reply(game_id: str, response: HumanResponse) -> dict[str, bool]:
        path = journal_path(game_id)
        if not manager.is_running(game_id):
            raise HTTPException(status_code=409, detail="The game is paused. Resume it before playing.")
        if _restore(path).is_finished:
            raise HTTPException(status_code=409, detail="The game has finished.")
        try:
            submit_response(mailbox_for(path), response)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return {"accepted": True}
