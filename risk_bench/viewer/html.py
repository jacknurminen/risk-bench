import json
from pathlib import Path
from typing import Any

import jinja2

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "game.html.j2"


def render_viewer_html(view_model: dict[str, Any]) -> str:
    """Render a game view model into a self-contained HTML replay viewer.

    Args:
        view_model: The dict produced by ``snapshots.build_view_model``.

    Returns:
        A complete HTML document with the game data embedded; no server needed.
    """
    template = jinja2.Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.render(game_json=json.dumps(view_model))
