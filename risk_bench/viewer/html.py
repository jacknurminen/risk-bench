import json
from pathlib import Path
from typing import Any

import jinja2

UI_DIRECTORY = Path(__file__).parents[2] / "ui"
_TEMPLATE_PATH = UI_DIRECTORY / "game.html.j2"
_BOARD_SVG_PATH = UI_DIRECTORY / "board.svg"

# loaded once at import: a running server must serve the template its own view-model
# code was loaded with, or the page's JS and the embedded data can skew apart
_TEMPLATE = jinja2.Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
_BOARD_SVG = _BOARD_SVG_PATH.read_text(encoding="utf-8")
_TOURNAMENT_TEMPLATE = jinja2.Template((UI_DIRECTORY / "tournament.html.j2").read_text(encoding="utf-8"))


def render_viewer_html(view_model: dict[str, Any], trace_url: str) -> str:
    """Render a game view model into a self-contained HTML replay viewer.

    Args:
        view_model: The dict produced by ``snapshots.build_view_model``.
        trace_url: The URL serving the game's API call trace, or '' when there
            is no server to ask (the static export).

    Returns:
        A complete static HTML document with the game data and board map embedded.
    """
    return _render(view_model=view_model, live=False, poll_url="", trace_url=trace_url)


def render_live_viewer_html(view_model: dict[str, Any], poll_url: str, trace_url: str) -> str:
    """Render the viewer for a possibly in-progress game that polls for updates.

    Args:
        view_model: The current view model; the page keeps itself fresh afterwards.
        poll_url: The URL the page polls for the updated view model JSON.
        trace_url: The URL serving the game's API call trace.

    Returns:
        A complete HTML document that follows the game as it grows.
    """
    return _render(view_model=view_model, live=True, poll_url=poll_url, trace_url=trace_url)


def render_tournament_html(view_model: dict[str, Any], live: bool, poll_url: str) -> str:
    """Render a tournament's cross-game statistics page.

    Args:
        view_model: The dict produced by ``tournament.build_tournament_view_model``.
        live: Whether member games are still running and the page should poll.
        poll_url: The URL the page polls for the updated view model JSON.

    Returns:
        A complete HTML document.
    """
    return _TOURNAMENT_TEMPLATE.render(
        tournament_json=json.dumps(view_model), live_json=json.dumps(live), poll_url_json=json.dumps(poll_url)
    )


def _render(view_model: dict[str, Any], live: bool, poll_url: str, trace_url: str) -> str:
    return _TEMPLATE.render(
        game_json=json.dumps(view_model),
        live_json=json.dumps(live),
        poll_url_json=json.dumps(poll_url),
        trace_url_json=json.dumps(trace_url),
        board_svg=_BOARD_SVG,
    )
