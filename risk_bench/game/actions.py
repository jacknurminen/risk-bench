from __future__ import annotations

from game.board import Territory
from pydantic import BaseModel, ConfigDict, Field


class _Action(BaseModel):
    """Base for all agent actions: a validated, schema-bearing tool call.

    Each subclass is simultaneously the JSON schema sent to the model (via
    ``model_json_schema``), the validator for the arguments the model returns
    (via ``model_validate``), and the typed action the engine applies.
    """

    model_config = ConfigDict(extra="forbid")


# --- Reinforce phase ---


class PlaceTroops(_Action):
    """Place troops on a territory you own."""

    territory: Territory = Field(description="A territory you own to place troops on")
    count: int = Field(ge=1, description="Number of troops to place")


class RedeemCards(_Action):
    """Redeem a set of three cards for bonus troops."""

    cards: list[str] = Field(
        min_length=3,
        max_length=3,
        description="Exactly three cards from your hand forming a valid set",
    )


# --- Attack phase ---


class Attack(_Action):
    """Attack an adjacent enemy territory from one of your territories."""

    source: Territory = Field(description="A territory you own to attack from (must have 2+ troops)")
    target: Territory = Field(description="An adjacent enemy territory to attack")
    armies: int = Field(ge=1, le=3, description="Number of armies to attack with (1-3)")


class EndAttacks(_Action):
    """Stop attacking and end the attack phase."""


# --- Fortify phase ---


class Fortify(_Action):
    """Move troops once between two connected territories you own."""

    source: Territory = Field(description="A territory you own to move troops from")
    target: Territory = Field(description="A connected territory you own to move troops to")
    count: int = Field(ge=1, description="Number of troops to move")


class Skip(_Action):
    """Make no fortify move and end your turn."""


# --- Per-phase action unions ---

ReinforceAction = PlaceTroops | RedeemCards
AttackAction = Attack | EndAttacks
FortifyAction = Fortify | Skip
