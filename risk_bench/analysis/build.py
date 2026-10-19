"""V3 analysis: populations, outcomes, missing evidence and independent coder comparisons."""

from pathlib import Path

from risk_bench.analysis.metrics import comparisons, member_summary
from risk_bench.runner.evaluation_store import evaluation_header, read_evaluation
from risk_bench.runner.evaluation_versions import EvaluationVersion
from risk_bench.viewer.evaluation import build_evaluation_view_model

SCHEMA_VERSION = 5


def build_analysis(journal_path: Path, eval_path: Path, generated_at: str) -> dict:
    header = evaluation_header(read_evaluation(eval_path))
    if header is None:
        raise ValueError("No v3 evaluation is available to analyze.")
    version = EvaluationVersion(
        header.run_id,
        header.created_at,
        header.panel,
        True,
        eval_path,
        eval_path.with_suffix(".trace.jsonl"),
        eval_path.with_suffix(".analysis.json"),
        # Human verification never changes automated counts or agreement.
        eval_path.with_suffix(".unused-verifications"),
    )
    view = build_evaluation_view_model(journal_path, version)
    return {
        "schema_version": header.schema_version,
        "game_id": journal_path.parent.name,
        "generated_at": generated_at,
        "codebook_version": header.codebook_version,
        "codebook_hash": header.codebook_hash,
        "source": {
            **header.source,
            "panel": header.panel,
            **(view["coverage"] or {}),
            "independent_observation": not bool(header.source.get("ancestry")),
        },
        "members": [member_summary(m) for m in view["members"]],
        "comparison": comparisons(view["members"]),
        "research_report": view["research_report"],
        "notes": [
            "Counts are per coder and unique tag; panel votes are not independent observations.",
            "Proposals without establishment are counted separately from promises.",
            "Adherence uses only valid, established, resolved agent-attributable commitments.",
            ("Documented deception is one incident across evidence bases; candidate reviews are separate."
             if header.schema_version >= 6 else
             "Deceptive is one statement status and includes the underlying contradiction."),
            "Citation integrity checks sources, not the truth of an interpretation.",
            "Forks share observations with their ancestors and are not independent games.",
        ] if header.schema_version >= 4 else [
            "Legacy v2 codebook and seven-code counts; do not relabel as two-tag assessments.",
            "Forks share observations with their ancestors and are not independent games.",
        ],
    }
