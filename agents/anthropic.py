from __future__ import annotations

import json
from typing import Any

import anthropic

from agents.base import BaseAgent
from game.state import GameState, Phase

_SYSTEM_PROMPT = """You are playing the board game Risk. Your goal is to win by completing your secret mission.

On each turn you will be told your current phase and must respond with the appropriate tool call.

Key rules:
- REINFORCE: Place all your troops before the turn advances. You may also redeem card sets.
- ATTACK: Attack from territories you own (need 2+ troops) into adjacent enemy territories. End when done.
- FORTIFY: Move troops once between two connected owned territories, or skip.

Think strategically about territory control, continent bonuses, and your mission."""

_CHAT_SYSTEM = """You are playing Risk. You can communicate with other players via the group chat.
Keep messages brief and strategic. You may negotiate, bluff, or stay silent (return empty list)."""

# Tool schemas for each phase
_TOOLS: dict[str, list[dict[str, Any]]] = {
    "reinforce": [
        {
            "name": "place_troops",
            "description": "Place troops on a territory you own",
            "input_schema": {
                "type": "object",
                "properties": {
                    "territory": {"type": "string", "description": "Territory name to reinforce"},
                    "count": {"type": "integer", "description": "Number of troops to place"},
                },
                "required": ["territory", "count"],
            },
        },
        {
            "name": "redeem_cards",
            "description": "Redeem a set of 3 cards for bonus troops",
            "input_schema": {
                "type": "object",
                "properties": {
                    "cards": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exactly 3 card strings from your hand",
                    }
                },
                "required": ["cards"],
            },
        },
    ],
    "attack": [
        {
            "name": "attack",
            "description": "Attack an adjacent enemy territory",
            "input_schema": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "Your territory to attack from (must have 2+ troops)"},
                    "to": {"type": "string", "description": "Adjacent enemy territory to attack"},
                    "attackers": {"type": "integer", "description": "Number of armies to attack with (1-3)"},
                },
                "required": ["from", "to", "attackers"],
            },
        },
        {
            "name": "end_attacks",
            "description": "End your attack phase",
            "input_schema": {"type": "object", "properties": {}},
        },
    ],
    "fortify": [
        {
            "name": "fortify",
            "description": "Move troops between two connected owned territories",
            "input_schema": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "Territory to move troops from"},
                    "to": {"type": "string", "description": "Territory to move troops to (must be connected)"},
                    "count": {"type": "integer", "description": "Number of troops to move"},
                },
                "required": ["from", "to", "count"],
            },
        },
        {
            "name": "skip_fortify",
            "description": "Skip the fortify phase",
            "input_schema": {"type": "object", "properties": {}},
        },
    ],
}

_CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "say",
        "description": "Broadcast a message to all players",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Your message (max 200 chars)"}},
            "required": ["text"],
        },
    },
    {
        "name": "pass_turn",
        "description": "Stay silent this window",
        "input_schema": {"type": "object", "properties": {}},
    },
]


class AnthropicAgent(BaseAgent):
    """Risk agent backed by the Anthropic Claude API using tool use."""

    def __init__(
        self,
        color: str,
        name: str,
        model: str,
        api_key: str | None,
        max_tokens: int,
    ) -> None:
        super().__init__(color=color, name=name)
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic(api_key=api_key)

    def get_move(self, state: GameState, phase: Phase) -> dict[str, Any]:
        """Return a move for the current phase by calling the Claude API.

        Args:
            state: Current game state.
            phase: The phase to generate a move for.

        Returns:
            Move dict appropriate for the given phase.
        """
        view = state.to_agent_view(color=self.color)
        phase_key = str(phase).replace("Phase.", "").lower()
        tools = _TOOLS.get(phase_key, [])

        user_content = (
            f"Game state:\n{json.dumps(view, indent=2)}\n\n"
            f"You are in the {phase_key.upper()} phase. "
            f"You have {view.get('your_cards', [])} cards. "
            f"Troops to place: {state.troops_to_place}. "
            f"Make your move now."
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM_PROMPT,
            tools=tools,  # type: ignore[arg-type]
            messages=[{"role": "user", "content": user_content}],
        )

        return self._parse_response(response=response, phase_key=phase_key)

    def get_messages(self, state: GameState, inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return chat messages by calling the Claude API.

        Args:
            state: Current game state.
            inbox: Recent messages from other players.

        Returns:
            List of message dicts with a 'text' key, or empty list to pass.
        """
        view = state.to_agent_view(color=self.color)
        user_content = (
            f"Game state summary:\n"
            f"Turn {view['turn']}, you are {self.color}.\n"
            f"Recent chat:\n"
            + "\n".join(f"[{entry['from']}]: {entry['text']}" for entry in view["chat_log"][-5:])
            + "\n\nRespond with say(text) or pass_turn()."
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=_CHAT_SYSTEM,
                tools=_CHAT_TOOLS,  # type: ignore[arg-type]
                messages=[{"role": "user", "content": user_content}],
            )
            for block in response.content:
                if block.type == "tool_use" and block.name == "say":
                    text = block.input.get("text", "")[:200]
                    if text:
                        return [{"text": text}]
        except Exception:
            pass
        return []

    def _parse_response(self, response: Any, phase_key: str) -> dict[str, Any]:
        for block in response.content:
            if block.type == "tool_use":
                tool_name = block.name
                inputs = block.input or {}

                if tool_name == "place_troops":
                    return {"territory": inputs["territory"], "count": inputs["count"]}
                if tool_name == "redeem_cards":
                    return {"action": "redeem_cards", "cards": inputs["cards"]}
                if tool_name == "attack":
                    return {"from": inputs["from"], "to": inputs["to"], "attackers": inputs["attackers"]}
                if tool_name == "end_attacks":
                    return {"action": "end_attacks"}
                if tool_name == "fortify":
                    return {"from": inputs["from"], "to": inputs["to"], "count": inputs["count"]}
                if tool_name == "skip_fortify":
                    return {"action": "skip"}

        # No tool call — return phase-appropriate default
        if phase_key == "attack":
            return {"action": "end_attacks"}
        return {"action": "skip"}
