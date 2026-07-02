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
_READER_TEMPLATE = jinja2.Template((UI_DIRECTORY / "reader.html.j2").read_text(encoding="utf-8"))
_TOURNAMENT_TEMPLATE = jinja2.Template((UI_DIRECTORY / "tournament.html.j2").read_text(encoding="utf-8"))
_EVALUATION_TEMPLATE = jinja2.Template((UI_DIRECTORY / "evaluation.html.j2").read_text(encoding="utf-8"))


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


def render_reader_html(
    game_view_model: dict[str, Any],
    evaluation_view_model: dict[str, Any],
    live: bool,
    live_running: bool,
    poll_url: str,
    eval_poll_url: str,
    evaluate_url: str,
    annotations_url: str,
    trace_url: str,
    classic_url: str,
) -> str:
    """Render the unified evaluation reader: the game timeline with the panel and annotations merged in.

    Args:
        game_view_model: The dict produced by ``snapshots.build_view_model`` (board, trace, stats).
        evaluation_view_model: The dict produced by ``evaluation.build_evaluation_view_model`` (spans,
            panel tags, human annotations, agreement, vocabulary).
        live: Whether the game is unfinished and the page should poll for updates.
        live_running: Whether the game's worker is currently playing (vs paused), so the page can
            show the right pause/resume control; only meaningful when ``live`` is True.
        poll_url: The URL the page polls for the updated game view model JSON.
        eval_poll_url: The URL the page polls (and re-fetches after annotating/evaluating) for the
            evaluation view model JSON.
        evaluate_url: The URL the page POSTs an evaluator panel to, to start a run.
        annotations_url: The base URL for human-annotation CRUD.
        trace_url: The URL serving the game's API call trace (used by the classic view).
        classic_url: The URL of the classic cockpit view, where the stats modal still lives.

    Returns:
        A complete HTML document with the game data, evaluation, and board map embedded.
    """
    return _READER_TEMPLATE.render(
        game_json=json.dumps(game_view_model),
        eval_json=json.dumps(evaluation_view_model),
        live_json=json.dumps(live),
        live_running_json=json.dumps(live_running),
        poll_url_json=json.dumps(poll_url),
        eval_poll_url_json=json.dumps(eval_poll_url),
        evaluate_url_json=json.dumps(evaluate_url),
        annotations_url_json=json.dumps(annotations_url),
        trace_url_json=json.dumps(trace_url),
        classic_url_json=json.dumps(classic_url),
        board_svg=_BOARD_SVG,
    )


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


def render_evaluation_html(view_model: dict[str, Any], game_id: str, poll_url: str, evaluate_url: str) -> str:
    """Render the evaluation page: configure-and-run controls plus the panel's results.

    Args:
        view_model: The dict produced by ``evaluation.build_evaluation_view_model``.
        game_id: The game being evaluated, for the back-link and titles.
        poll_url: The URL the page polls for the updated evaluation view model JSON.
        evaluate_url: The URL the page POSTs an evaluator panel to, to start a run.

    Returns:
        A complete HTML document.
    """
    return _EVALUATION_TEMPLATE.render(
        evaluation_json=json.dumps(view_model),
        game_id_json=json.dumps(game_id),
        poll_url_json=json.dumps(poll_url),
        evaluate_url_json=json.dumps(evaluate_url),
    )


def _render(view_model: dict[str, Any], live: bool, poll_url: str, trace_url: str) -> str:
    return _TEMPLATE.render(
        game_json=json.dumps(view_model),
        live_json=json.dumps(live),
        poll_url_json=json.dumps(poll_url),
        trace_url_json=json.dumps(trace_url),
        board_svg=_BOARD_SVG,
    )
