import json
import re

from risk_bench.agents.base import Response, ToolCall
from risk_bench.config import Settings
from risk_bench.launch import run_evaluation
from risk_bench.runner.evaluation import Evaluator
from risk_bench.runner.evaluation_versions import list_versions
from risk_bench.runner.journal import JournalHeader, JournalWriter
from risk_bench.viewer.evaluation import build_evaluation_view_model
from tests.runner.test_continuation import NAMES, players, runner


class NoCandidateCoder:
    def respond(self, messages, tools):
        if tools[0].name == "submit_extraction":
            match = re.search(r"\[[\d, ]+\]", messages[1].content)
            assert match is not None
            spans = json.loads(match[0])
            result = {"messages": [{"span": s, "status": "no_candidate", "explanation": "Screened."} for s in spans]}
        else:
            result = {"agreements": [], "episodes": [], "claims": [], "memos": ["No candidate in this test."]}
        return Response(None, (ToolCall("1", tools[0].name, result),))


def test_full_pipeline_preserves_game_and_versions_each_coder_run(tmp_path, monkeypatch):
    monkeypatch.setattr("risk_bench.runner.journal.os.fsync", lambda fd: None)
    journal = tmp_path / "game.jsonl"
    writer = JournalWriter(journal, False)
    writer.write_header(JournalHeader(4, 19, dict.fromkeys(NAMES, "random"), 1, True, 1, []))
    runner(players(), writer, turns=1).run()
    writer.close()
    original = journal.read_bytes()
    monkeypatch.setattr(
        "risk_bench.launch.specs.build_evaluators",
        lambda evaluator_specs, settings, trace: [Evaluator(NoCandidateCoder(), spec) for spec in evaluator_specs],
    )
    for _ in range(2):
        run_evaluation(journal, ["test:a", "test:b"], Settings())
    versions = list_versions(journal)
    assert len(versions) == 2 and versions[0].run_id != versions[1].run_id
    for version in versions:
        view = build_evaluation_view_model(journal, version)
        assert view["coverage"]["source_matches"]
        assert all(m["status"] == "complete" and m["coverage"] for m in view["members"])
        analysis = json.loads(version.analysis_path.read_text())
        assert all(m["adherence"]["proportion"] is None for m in analysis["members"])
    assert journal.read_bytes() == original
