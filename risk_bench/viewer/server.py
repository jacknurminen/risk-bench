import http.server
import json
from pathlib import Path

from risk_bench.viewer.html import render_viewer_html
from risk_bench.viewer.snapshots import build_view_model


def create_viewer_server(journal_path: Path, host: str, port: int) -> http.server.ThreadingHTTPServer:
    """Create an HTTP server that serves a live-updating viewer for a game journal.

    The journal is re-read and replayed through the engine on every request:
    games are small enough that a full replay takes milliseconds, which keeps
    the server completely stateless — it can be started before, during, or
    after the game it is watching.

    Args:
        journal_path: The game journal to follow; it may still be growing.
        host: The interface to bind to.
        port: The port to bind to (0 picks a free one).

    Returns:
        The bound server; call ``serve_forever()`` on it to start handling requests.
    """
    bound_handler = type("BoundViewerHandler", (_ViewerRequestHandler,), {"journal_path": journal_path})
    return http.server.ThreadingHTTPServer((host, port), bound_handler)


class _ViewerRequestHandler(http.server.BaseHTTPRequestHandler):
    journal_path: Path

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        match self.path:
            case "/" | "/index.html":
                html = render_viewer_html(view_model=build_view_model(journal_path=self.journal_path), live=True)
                self._respond(body=html.encode("utf-8"), content_type="text/html; charset=utf-8")
            case "/game.json":
                view_model = build_view_model(journal_path=self.journal_path)
                self._respond(body=json.dumps(view_model).encode("utf-8"), content_type="application/json")
            case _:
                self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib hook signature
        pass  # keep the console clean; the game's own verbose output is the interesting stream

    def _respond(self, body: bytes, content_type: str) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # polling clients come and go; a mid-response disconnect is routine, not an error
