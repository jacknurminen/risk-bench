import json

import pytest

from risk_bench.config import Settings
from risk_bench.launch import run_evaluation, run_new_game
from risk_bench.runner.evaluation import EvaluatedGame
from risk_bench.runner.evaluation_config import configure_panel, configured_panel
from risk_bench.runner.evaluation_versions import list_versions
from risk_bench.runner.journal import read_journal
from risk_bench.runner.live_evaluation import review_activity_for_journal, run_live_evaluation
from risk_bench.viewer.tournament import build_tournament_view_model
from tests.runner.test_live_evaluation import DeterministicEvaluator


@pytest.mark.parametrize("live,whole", [(False, False), (True, False), (False, True), (True, True)])
def test_independent_modes_run_at_their_boundaries_and_keep_versions(tmp_path, monkeypatch, live, whole):
    journal = tmp_path / "game" / "game.jsonl"
    journal.parent.mkdir()
    round_coder = DeterministicEvaluator()
    calls = []

    class WholeCoder:
        spec = "test:whole:high"

        def evaluate_game(self, players, spans, game_overview):
            assert read_journal(journal)[-1]["type"] == "result"
            assert review_activity_for_journal(journal)["kind"] == "whole_game"
            assert review_activity_for_journal(journal)["panel"] == [self.spec]
            if live:
                assert round_coder.final_calls >= 1
            calls.append("whole")
            return EvaluatedGame(self.spec, ({"type": "eval_member_status", "status": "complete", "memos": []},))

    def build(**kwargs):
        return (round_coder,) if kwargs["evaluator_specs"] == [round_coder.spec] else (WholeCoder(),)

    monkeypatch.setattr("risk_bench.specs.build_evaluators", build)
    configure_panel(journal, "live_rounds", [round_coder.spec] if live else [])
    configure_panel(journal, "whole_game", [WholeCoder.spec] if whole else [])
    run_new_game(["random"] * 3, 19, Settings(), [], journal, 4, False, [])
    assert bool(round_coder.calls) is live
    assert calls == (["whole"] if whole else [])
    assert review_activity_for_journal(journal) is None
    versions = list_versions(journal)
    assert {v.method for v in versions} == (
        ({"live_rounds"} if live else set()) | ({"whole_game"} if whole else set())
    )
    if live and whole:
        original = journal.read_bytes()
        run_live_evaluation(journal, [round_coder.spec], Settings(), follow=False, restart=True)
        run_evaluation(journal, [WholeCoder.spec], Settings())
        assert journal.read_bytes() == original
        assert [v.method for v in list_versions(journal)] == [
            "whole_game", "live_rounds", "whole_game", "live_rounds",
        ]
        assert configured_panel(journal, "live_rounds") == [round_coder.spec]
        assert configured_panel(journal, "whole_game") == [WholeCoder.spec]
        (tmp_path / "tournament.json").write_text(json.dumps({
            "id": "test", "game_ids": ["game"], "players": ["random"] * 3,
        }))
        report = build_tournament_view_model(tmp_path)["research"]
        assert len(report["availability"]) == 2  # latest run of each method, not all four runs
        assert {row["method"] for row in report["availability"]} == {"live_rounds", "whole_game"}


def test_failed_whole_game_setup_keeps_finished_game_and_clears_activity(tmp_path, monkeypatch):
    journal = tmp_path / "game.jsonl"
    configure_panel(journal, "whole_game", ["test:whole"])

    def fail(**kwargs):
        raise ValueError("Model unavailable")

    monkeypatch.setattr("risk_bench.specs.build_evaluators", fail)
    run_new_game(["random"] * 3, 19, Settings(), [], journal, 1, False, [])
    assert read_journal(journal)[-1]["type"] == "result"
    assert review_activity_for_journal(journal) is None
    assert "Model unavailable" in journal.with_suffix(".eval.error.json").read_text()
