from pathlib import Path

from fastapi.testclient import TestClient

from risk_bench.api.app import create_app
from tests.runner.test_journal import write_game_journal
from tests.viewer.test_tournament import tournament_directory_with_games


def test_a_game_without_a_description_lists_and_views_empty_fields(tmp_path: Path):
    client = _client_with_standalone_game(games_directory=tmp_path)

    [game] = client.get("/api/games").json()["games"]

    assert (game["title"], game["description"]) == ("", "")
    assert client.get("/api/games/first-game").json()["about"] == {"title": "", "description": ""}


def test_a_saved_game_description_is_trimmed_listed_and_shown_in_the_reader(tmp_path: Path):
    client = _client_with_standalone_game(games_directory=tmp_path)

    response = client.put(
        "/api/games/first-game/description", json={"title": "  Pilot game  ", "description": "First try.\n"}
    )

    assert response.json() == {"title": "Pilot game", "description": "First try."}
    assert (tmp_path / "first-game" / "description.json").is_file()
    [game] = client.get("/api/games").json()["games"]
    assert (game["title"], game["description"]) == ("Pilot game", "First try.")
    assert client.get("/api/games/first-game").json()["about"]["title"] == "Pilot game"
    assert "/api/games/first-game/description" in client.get("/games/first-game").text


def test_a_tournament_description_is_saved_beside_its_manifest(tmp_path: Path):
    directory = tournament_directory_with_games(tmp_path=tmp_path, seeds=[11, 12])
    client = TestClient(create_app(games_directory=tmp_path))

    response = client.put(
        f"/api/tournaments/{directory.name}/description",
        json={"title": "Effort study", "description": "Low, medium and high effort."},
    )

    assert response.status_code == 200
    [tournament] = client.get("/api/games").json()["tournaments"]
    assert (tournament["title"], tournament["description"]) == ("Effort study", "Low, medium and high effort.")
    assert client.get(f"/api/tournaments/{directory.name}").json()["about"]["title"] == "Effort study"
    member_games = client.get("/api/games").json()["games"]
    assert all(game["title"] == "" for game in member_games)  # a tournament's note is not copied to its games


def test_descriptions_for_unknown_games_and_tournaments_are_404s(tmp_path: Path):
    client = TestClient(create_app(games_directory=tmp_path))
    body = {"title": "x", "description": ""}

    assert client.put("/api/games/missing/description", json=body).status_code == 404
    assert client.put("/api/tournaments/missing/description", json=body).status_code == 404


def test_an_overlong_title_is_rejected(tmp_path: Path):
    client = _client_with_standalone_game(games_directory=tmp_path)

    response = client.put("/api/games/first-game/description", json={"title": "x" * 201, "description": ""})

    assert response.status_code == 422
    assert not (tmp_path / "first-game" / "description.json").exists()


def _client_with_standalone_game(games_directory: Path) -> TestClient:
    game_directory = games_directory / "first-game"
    game_directory.mkdir(parents=True)
    write_game_journal(path=game_directory / "game.jsonl", seed=11, max_turns=2_000)
    return TestClient(create_app(games_directory=games_directory))
