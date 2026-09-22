"""Browser-controlled player, with a durable mailbox shared by API and game worker.

Only the worker advances the game. Requests contain a filtered PlayerView and
legal action templates; replies are bound to a unique decision and validated
both by the API and by the worker. Waiting never substitutes a random move.
"""

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
from risk_bench.game.serialization import action_from_json, action_to_json
from risk_bench.players.base import Decision, DecisionRequest, NegotiationTurn, PlayerView
from risk_bench.players.rendering import narrate_event
from risk_bench.runner.journal import read_journal


class HumanPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action_index: int = Field(ge=0)
    count: int = Field(ge=1)


class HumanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str
    placements: list[HumanPlacement] | None = Field(default=None, min_length=1, max_length=42)
    action_index: int | None = Field(default=None, ge=0)
    count: int | None = Field(default=None, ge=1)
    attacker_dice: int | None = Field(default=None, ge=1, le=3)
    max_attacks: int | None = Field(default=None, ge=1)
    armies_to_leave_behind: int | None = Field(default=None, ge=1)
    message: str = Field(default="", max_length=4000)
    recipient: str | None = None
    thought: str = Field(default="", max_length=8000)
    notes: str | None = Field(default=None, max_length=8000)


def mailbox_for(journal_path: Path) -> Path:
    return journal_path.with_suffix(".human")


def view_to_json(view: PlayerView) -> dict[str, Any]:
    return {
        "player_id": view.player_id,
        "phase": view.phase,
        "turn_number": view.turn_number,
        "round_number": view.round_number,
        "previous_round_number": view.previous_round_number,
        "current_player": view.current_player,
        "private_messages_allowed": view.private_messages_allowed,
        "armies": view.armies,
        "owner": view.owner,
        "players": [asdict(player) for player in view.players],
        "own_cards": [asdict(card) for card in view.own_cards],
        "mission": view.own_mission.description,
        "armies_to_place": view.armies_to_place,
        "notes": view.notes,
        "additional_context": view.additional_context,
        "chat": [asdict(message) for message in view.visible_chat],
        "events": [narrate_event(event) for event in view.recent_events],
    }


def read_prompt(mailbox: Path) -> dict[str, Any] | None:
    try:
        return json.loads((mailbox / "request.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _write_atomic(path: Path, data: dict[str, Any], exclusive: bool = False) -> None:
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data), encoding="utf-8")
        if exclusive:
            os.link(temporary, path)  # publish a complete reply, rejecting double submissions
        else:
            temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_response(prompt: dict[str, Any], response: HumanResponse) -> Decision | NegotiationTurn | str | None:
    if response.request_id != prompt["request_id"] or not prompt["waiting"]:
        raise ValueError("This decision has expired. Refresh and use the current controls.")
    if prompt["kind"] == "notes":
        if response.model_dump(exclude={"request_id", "notes"}, exclude_defaults=True):
            raise ValueError("Only private notes may be submitted during note-taking.")
        return response.notes.strip() if response.notes is not None else None
    if response.notes is not None:
        raise ValueError("Notes can only be saved during note-taking.")
    view = prompt["view"]
    message = None
    if response.recipient is not None:
        recipients = {
            player["player_id"]
            for player in view["players"]
            if not player["is_eliminated"] and player["player_id"] != view["player_id"]
        }
        if not view["private_messages_allowed"] or response.recipient not in recipients:
            raise ValueError("That private-message recipient is not available.")
    if response.message.strip():
        message = ChatMessage(
            turn_number=view["turn_number"],
            sender=view["player_id"],
            visibility=MessageVisibility.PRIVATE if response.recipient else MessageVisibility.PUBLIC,
            recipient=response.recipient,
            text=response.message.strip(),
        )
    numeric = {
        key: getattr(response, key)
        for key in ("count", "attacker_dice", "max_attacks", "armies_to_leave_behind")
        if getattr(response, key) is not None
    }
    if prompt["kind"] == "negotiation":
        if response.action_index is not None or numeric or response.placements is not None:
            raise ValueError("Only a message or silence is allowed during diplomacy.")
        return NegotiationTurn(messages=(message,) if message else (), thought=response.thought.strip() or None)
    if response.placements is not None:
        if response.action_index is not None or numeric:
            raise ValueError("Submit either a placement plan or a single action, not both.")
        actions = []
        seen = set()
        available = 0
        for placement in response.placements:
            if placement.action_index >= len(prompt["actions"]):
                raise ValueError("Choose one of your available placement territories.")
            template = prompt["actions"][placement.action_index]
            if template["kind"] != "place_armies":
                raise ValueError("Only army placements can be combined in a placement plan.")
            if template["territory"] in seen:
                raise ValueError("List each placement territory only once.")
            seen.add(template["territory"])
            available = template["count"]
            actions.append(action_from_json({**template, "count": placement.count}))
        if sum(placement.count for placement in response.placements) != available:
            raise ValueError(f"Allocate all {available} armies before committing your placements.")
        return Decision(actions=tuple(actions), chat_message=message, thought=response.thought.strip() or None)
    index = response.action_index
    if index is None or index >= len(prompt["actions"]):
        raise ValueError("Choose one of the available actions.")
    action = dict(prompt["actions"][index])
    allowed = {
        "place_armies": {"count"},
        "fortify": {"count"},
        "attack": {"attacker_dice", "max_attacks", "armies_to_leave_behind"},
    }.get(action["kind"], set())
    if numeric.keys() - allowed:
        raise ValueError("These controls do not apply to the selected action.")
    for key, value in numeric.items():
        if key in {"count", "attacker_dice"} and value > action[key]:
            raise ValueError(f"{key.replace('_', ' ')} must be at most {action[key]}.")
        action[key] = value
    return Decision(
        actions=(action_from_json(action),), chat_message=message, thought=response.thought.strip() or None
    )


def submit_response(mailbox: Path, response: HumanResponse) -> None:
    prompt = read_prompt(mailbox)
    if prompt is None:
        raise ValueError("The game is not waiting for human input.")
    parse_response(prompt, response)
    # Use the server-generated id, never a client-supplied path segment.
    reply_path = mailbox / f"{prompt['request_id']}.json"
    try:
        _write_atomic(reply_path, response.model_dump(), exclusive=True)
    except FileExistsError:
        raise ValueError("This decision was already submitted.") from None


class HumanPlayer:
    def __init__(self, player_id: str, mailbox: Path) -> None:
        self.player_id = player_id
        self.invalid_action_count = 0
        self.mailbox = mailbox
        self._saved_notes: str | None = None
        journal_path = mailbox.with_suffix(".jsonl")
        if journal_path.exists():
            for record in read_journal(journal_path):
                if record["type"] == "note" and record["player"] == player_id:
                    self._saved_notes = record["text"]
        mailbox.mkdir(parents=True, exist_ok=True)
        # A resumed worker issues fresh requests; an old browser reply cannot carry over.
        (mailbox / "request.json").unlink(missing_ok=True)
        for reply in mailbox.glob("*.json"):
            reply.unlink()

    def decide_action(self, request: DecisionRequest) -> Decision:
        answer = self._ask(request.view, [action_to_json(action) for action in request.legal_actions], "decision")
        assert isinstance(answer, Decision)
        return answer

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        answer = self._ask(view, [], "negotiation")
        assert isinstance(answer, NegotiationTurn)
        return answer

    def update_notes(self, view: PlayerView) -> str | None:
        answer = self._ask(view, [], "notes")
        assert answer is None or isinstance(answer, str)
        if answer is not None:
            self._saved_notes = answer
        return answer

    def _ask(
        self, view: PlayerView, actions: list[dict[str, Any]], kind: str
    ) -> Decision | NegotiationTurn | str | None:
        prompt = {
            "request_id": uuid4().hex,
            "waiting": True,
            "kind": kind,
            "view": view_to_json(view),
            "actions": actions,
        }
        # The runner resumes at the next board decision; retain the human's saved
        # notebook even when its in-memory notes have been reset on resume.
        prompt["view"]["notes"] = view.notes if view.notes is not None else self._saved_notes
        if kind == "notes":
            prompt["finished_turn"] = view.turn_number - 1
            prompt["finished_round"] = view.previous_round_number
        _write_atomic(self.mailbox / "request.json", prompt)
        reply_path = self.mailbox / f"{prompt['request_id']}.json"
        while True:
            if reply_path.exists():
                response = HumanResponse.model_validate_json(reply_path.read_text(encoding="utf-8"))
                answer = parse_response(prompt, response)
                _write_atomic(self.mailbox / "request.json", {**prompt, "waiting": False})
                reply_path.unlink()
                return answer
            time.sleep(0.1)
