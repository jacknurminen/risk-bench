import json
import re
from pathlib import Path
from typing import Any

import jinja2

from risk_bench.game.missions import mission_deck

UI_DIRECTORY = Path(__file__).parents[2] / "ui"
_BOARD_SVG_PATH = UI_DIRECTORY / "board.svg"

# loaded once at import: a running server must serve the skeleton its own view-model code
# was loaded with; the reader's CSS/JS live under /assets and are read from disk per request
_BOARD_SVG = _BOARD_SVG_PATH.read_text(encoding="utf-8")
_READER_TEMPLATE = jinja2.Template((UI_DIRECTORY / "reader" / "reader.html.j2").read_text(encoding="utf-8"))
_TOURNAMENT_TEMPLATE = jinja2.Template((UI_DIRECTORY / "tournament.html.j2").read_text(encoding="utf-8"))
_REVIEW_TEMPLATE = jinja2.Template((UI_DIRECTORY / "review.html.j2").read_text(encoding="utf-8"))


def render_reader_html(
    game_view_model: dict[str, Any],
    evaluation_view_model: dict[str, Any],
    live: bool,
    live_running: bool,
    poll_url: str,
    eval_poll_url: str,
    evaluate_url: str,
    verifications_url: str,
    trace_url: str,
    analysis_url: str,
    human_url: str = "",
) -> str:
    """Render the unified evaluation reader: the game timeline with the panel's read merged in.

    Args:
        game_view_model: The dict produced by ``snapshots.build_view_model`` (board, trace, stats).
        evaluation_view_model: The dict produced by ``evaluation.build_evaluation_view_model`` (spans,
            panel tags with human verifications, agreement, vocabulary).
        live: Whether the game is unfinished and the page should poll for updates.
        live_running: Whether the game's worker is currently playing (vs paused), so the page can
            show the right pause/resume control; only meaningful when ``live`` is True.
        poll_url: The URL the page polls for the updated game view model JSON.
        eval_poll_url: The URL the page polls (and re-fetches after annotating/evaluating) for the
            evaluation view model JSON.
        evaluate_url: The URL the page POSTs an evaluator panel to, to start a run.
        verifications_url: The URL human tag-verifications are PUT to.
        trace_url: The URL serving the game's API call trace (feeds the stats modal's call inspector).
        analysis_url: The URL serving the evaluation-analysis artifact (feeds the stats modal's
            evaluation sheet).
        human_url: Optional decision mailbox URL, enabling the human turn card in live web games.

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
        verifications_url_json=json.dumps(verifications_url),
        trace_url_json=json.dumps(trace_url),
        analysis_url_json=json.dumps(analysis_url),
        human_url=human_url,
        human_url_json=json.dumps(human_url),
        mission_cards=_mission_reference([player["name"] for player in game_view_model["players"]])
        if game_view_model.get("human_players")
        else [],
        board_svg=_BOARD_SVG,
    )


def _mission_reference(player_ids: list[str]) -> list[dict[str, Any]]:
    cards = []
    for mission in mission_deck(player_ids):
        continents = {str(c).replace("_", " ").title(): str(c) for c in sorted(mission.target_continents)}
        parts = (
            re.split("(" + "|".join(re.escape(name) for name in continents) + ")", mission.description)
            if continents
            else [mission.description]
        )
        cards.append(
            {
                "parts": [{"text": text, "continent": continents.get(text)} for text in parts],
                "continents": " ".join(continents.values()),
            }
        )
    return cards


def render_reader_export_html(game_view_model: dict[str, Any], evaluation_view_model: dict[str, Any]) -> str:
    """Render the reader as one self-contained HTML file, for offline sharing of a finished game.

    Every /assets stylesheet and script reference is inlined, so the file works from disk with
    no server behind it. All action URLs are blank; the page hides the affordances that would
    need a server (evaluate, verify, the API-call inspector) and shows everything embedded:
    the full timeline, panel tags with evidence, and the stats charts.

    Args:
        game_view_model: The dict produced by ``snapshots.build_view_model``.
        evaluation_view_model: The dict produced by ``evaluation.build_evaluation_view_model``;
            panel tags and human verifications ride along when the sidecars exist.

    Returns:
        A complete static HTML document with all data and assets embedded.
    """
    html = render_reader_html(
        game_view_model=game_view_model,
        evaluation_view_model=evaluation_view_model,
        live=False,
        live_running=False,
        poll_url="",
        eval_poll_url="",
        evaluate_url="",
        verifications_url="",
        trace_url="",
        analysis_url="",
    )
    return _inline_assets(html=html)


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


def render_review_html(tournament_id: str) -> str:
    """Render the annotation review page; it loads samples and items from the API."""
    return _REVIEW_TEMPLATE.render(tournament_id_json=json.dumps(tournament_id))


def _inline_assets(html: str) -> str:
    def asset_content(match: re.Match[str]) -> str:
        return (UI_DIRECTORY / match.group(1)).read_text(encoding="utf-8")

    stylesheet_pattern = r'<link rel="stylesheet" href="/assets/([^"]+)">'
    script_pattern = r'<script src="/assets/([^"]+)"></script>'
    html = re.sub(stylesheet_pattern, lambda match: f"<style>\n{asset_content(match)}</style>", html)
    return re.sub(script_pattern, lambda match: f"<script>\n{asset_content(match)}</script>", html)
