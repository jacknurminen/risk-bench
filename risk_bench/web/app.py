from __future__ import annotations

from pathlib import Path

import storage.store as store
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Risk-Bench Dashboard")

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def leaderboard(request: Request) -> HTMLResponse:
    """Render the leaderboard page.

    Args:
        request: Incoming HTTP request.

    Returns:
        HTML response with the current leaderboard standings.
    """
    lb = store.load_leaderboard()
    if lb is None:
        lb = store.rebuild_leaderboard()
    return templates.TemplateResponse("leaderboard.html", {"request": request, "leaderboard": lb})


@app.get("/games", response_class=HTMLResponse)
async def games_list(request: Request) -> HTMLResponse:
    """Render the recent games list page.

    Args:
        request: Incoming HTTP request.

    Returns:
        HTML response listing the 50 most recent games.
    """
    games = store.list_games(limit=50)
    return templates.TemplateResponse("games.html", {"request": request, "games": games})


@app.get("/games/{game_id}", response_class=HTMLResponse)
async def game_detail(request: Request, game_id: str) -> HTMLResponse:
    """Render the detail page for a single game.

    Args:
        request: Incoming HTTP request.
        game_id: Unique identifier of the game to display.

    Returns:
        HTML response with the full game replay and chat log.

    Raises:
        HTTPException: 404 if the game_id is not found.
    """
    game = store.load_game(game_id=game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return templates.TemplateResponse("game_detail.html", {"request": request, "game": game})


@app.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_profile(request: Request, agent_id: str) -> HTMLResponse:
    """Render the profile page for a single agent.

    Args:
        request: Incoming HTTP request.
        agent_id: Unique identifier of the agent to display.

    Returns:
        HTML response with the agent's stats and game history.

    Raises:
        HTTPException: 404 if the agent_id is not found.
    """
    agent = store.get_agent(agent_id=agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    all_games = store.list_games(limit=200)
    agent_games = [game for game in all_games if agent_id in game.agents]

    return templates.TemplateResponse(
        "agent.html",
        {"request": request, "agent": agent, "games": agent_games},
    )


# JSON API endpoints for programmatic access
@app.get("/api/leaderboard")
async def api_leaderboard() -> dict[str, object]:
    """Return the current leaderboard as JSON.

    Returns:
        Serialized leaderboard dict.
    """
    lb = store.load_leaderboard()
    if lb is None:
        lb = store.rebuild_leaderboard()
    return lb.model_dump()


@app.get("/api/games")
async def api_games(limit: int = 20) -> list[dict[str, object]]:
    """Return recent games as JSON.

    Args:
        limit: Maximum number of games to return (query parameter).

    Returns:
        List of serialized game record dicts.
    """
    games = store.list_games(limit=limit)
    return [game.model_dump() for game in games]


@app.get("/api/agents")
async def api_agents() -> list[dict[str, object]]:
    """Return all registered agents as JSON.

    Returns:
        List of serialized agent record dicts.
    """
    return [agent.model_dump() for agent in store.load_agents()]
