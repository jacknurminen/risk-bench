"""Post-evaluation analysis: derived integrity / agreement / descriptive statistics.

The evaluator panel writes ``game.eval.jsonl`` (the instrument of record); this package reads
that sidecar back — together with the journal, the trace index, and the human verifications —
and writes ``game.analysis.json``, a re-runnable derived artifact of computed statistics next to
the game. Nothing here mutates the journal, the eval sidecar, or the verifications.
"""

from risk_bench.analysis.store import analysis_path_for_journal, write_analysis

__all__ = ["analysis_path_for_journal", "write_analysis"]
