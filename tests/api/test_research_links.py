import json
import re

from tests.api.test_app import client_with_two_evaluation_versions


def test_game_evidence_link_loads_its_pinned_version(tmp_path):
    client, game_id = client_with_two_evaluation_versions(tmp_path)
    response = client.get(f"/games/{game_id}?version=20260101-000000")
    assert response.status_code == 200
    match = re.search(r"evaluation: (.*),\n  live:", response.text)
    assert match
    evaluation = json.loads(match[1])
    assert evaluation["version"] == "20260101-000000"
    assert evaluation["members"][0]["episodes"][0]["id"] == "betrayal"
