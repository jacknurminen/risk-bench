"""FastAPI web dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import storage.store as store

app = FastAPI(title="Risk-Bench Dashboard")

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def leaderboard(request: Request) -> HTMLResponse:
    lb = store.load_leaderboard()
    if lb is None:
        lb = store.rebuild_leaderboard()
    return templates.TemplateResponse("leaderboard.html", {"request": request, "leaderboard": lb})


@app.get("/games", response_class=HTMLResponse)
async def games_list(request: Request) -> HTMLResponse:
    games = store.list_games(limit=50)
    return templates.TemplateResponse("games.html", {"request": request, "games": games})


@app.get("/games/{game_id}", response_class=HTMLResponse)
async def game_detail(request: Request, game_id: str) -> HTMLResponse:
    game = store.load_game(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return templates.TemplateResponse("game_detail.html", {"request": request, "game": game})


@app.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_profile(request: Request, agent_id: str) -> HTMLResponse:
    agent = store.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Load games involving this agent
    all_games = store.list_games(limit=200)
    agent_games = [g for g in all_games if agent_id in g.agents]

    return templates.TemplateResponse(
        "agent.html",
        {"request": request, "agent": agent, "games": agent_games},
    )


# JSON API endpoints for programmatic access
@app.get("/api/leaderboard")
async def api_leaderboard() -> dict:
    lb = store.load_leaderboard()
    if lb is None:
        lb = store.rebuild_leaderboard()
    return lb.model_dump()


@app.get("/api/games")
async def api_games(limit: int = 20) -> list:
    games = store.list_games(limit=limit)
    return [g.model_dump() for g in games]


@app.get("/api/agents")
async def api_agents() -> list:
    return [a.model_dump() for a in store.load_agents()]
