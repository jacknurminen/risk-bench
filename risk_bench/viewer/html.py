import json
from pathlib import Path
from typing import Any

import jinja2

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "game.html.j2"
_BOARD_SVG_PATH = Path(__file__).parent / "templates" / "board.svg"


def render_viewer_html(view_model: dict[str, Any], live: bool) -> str:
    """Render a game view model into an HTML replay viewer.

    Args:
        view_model: The dict produced by ``snapshots.build_view_model``.
        live: When True the page polls ``game.json`` for new steps, following
            an in-progress game; when False it is a self-contained replay of
            the embedded data, needing no server.

    Returns:
        A complete HTML document with the game data and board map embedded.
    """
    template = jinja2.Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.render(
        game_json=json.dumps(view_model),
        live_json=json.dumps(live),
        board_svg=_BOARD_SVG_PATH.read_text(encoding="utf-8"),
    )
