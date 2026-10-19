"""Experiment identity, independent of journal and evaluation storage formats."""

EXPERIMENT_VERSION = 3


def require_v3(header: dict) -> None:
    if header.get("experiment_version") != EXPERIMENT_VERSION:
        raise ValueError("This is not a v3 game. Open older games with the v2 Git tag; v3 requires fresh games.")
    if header.get("schema_version") != 4:
        raise ValueError("V3 requires journal schema 4 with complete execution checkpoints.")
