"""The title and free-text description a researcher attaches to a game or a tournament.

Both live in a ``description.json`` sidecar inside the game's or the tournament's folder. A sidecar
keeps them out of the append-only journal and out of ``tournament.json``, which the tournament
runner rewrites, and it travels with the folder when the data is copied or committed.
"""

from pathlib import Path

from pydantic import BaseModel, Field

DESCRIPTION_FILE_NAME = "description.json"


class Description(BaseModel):
    """A short human title and a longer note on what the game or tournament is for."""

    title: str = Field(max_length=200)
    description: str = Field(max_length=20_000)


def read_description(directory: Path) -> Description:
    """Read the description saved in a game or tournament folder.

    Args:
        directory: The game's or tournament's folder.

    Returns:
        The saved description, or an empty one when none has been written.
    """
    path = directory / DESCRIPTION_FILE_NAME
    if not path.is_file():
        return Description(title="", description="")
    return Description.model_validate_json(path.read_text(encoding="utf-8"))


def write_description(directory: Path, description: Description) -> Description:
    """Save a game's or tournament's description, trimming surrounding whitespace.

    Args:
        directory: The game's or tournament's folder; it must already exist.
        description: The title and description to save; empty strings clear them.

    Returns:
        The description as it was saved.
    """
    trimmed = Description(title=description.title.strip(), description=description.description.strip())
    path = directory / DESCRIPTION_FILE_NAME
    # written to a temporary file first so a concurrent read never sees half a file
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(trimmed.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)
    return trimmed
