from __future__ import annotations

import json
from typing import Any

import openai as _openai

from agents.base import BaseAgent
from game.state import GameState, Phase

_SYSTEM_PROMPT = """You are playing the board game Risk. Your goal is to win by completing your secret mission.

On each turn you will be told your current phase and must call the appropriate function.

Key rules:
- REINFORCE: Place all your troops. You may redeem card sets first.
- ATTACK: Attack adjacent enemy territories. End when done.
- FORTIFY: Move troops once between connected owned territories, or skip."""

_FUNCTIONS: dict[str, list[dict[str, Any]]] = {
    "reinforce": [
        {
            "type": "function",
            "function": {
                "name": "place_troops",
                "description": "Place troops on a territory you own",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "territory": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    "required": ["territory", "count"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "redeem_cards",
                "description": "Redeem 3 cards for bonus troops",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cards": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["cards"],
                },
            },
        },
    ],
    "attack": [
        {
            "type": "function",
            "function": {
                "name": "attack",
                "description": "Attack an adjacent enemy territory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                        "attackers": {"type": "integer"},
                    },
                    "required": ["from", "to", "attackers"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "end_attacks",
                "description": "End attack phase",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ],
    "fortify": [
        {
            "type": "function",
            "function": {
                "name": "fortify",
                "description": "Move troops between connected owned territories",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    "required": ["from", "to", "count"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "skip_fortify",
                "description": "Skip fortify phase",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ],
}


class OpenAIAgent(BaseAgent):
    """Risk agent backed by the OpenAI chat completions API using function calling."""

    def __init__(
        self,
        color: str,
        name: str,
        model: str,
        api_key: str | None,
        base_url: str | None,
        max_tokens: int,
    ) -> None:
        super().__init__(color=color, name=name)
        self.model = model
        self.max_tokens = max_tokens
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self.client = _openai.OpenAI(**kwargs)

    def get_move(self, state: GameState, phase: Phase) -> dict[str, Any]:
        """Return a move for the current phase by calling the OpenAI API.

        Args:
            state: Current game state.
            phase: The phase to generate a move for.

        Returns:
            Move dict appropriate for the given phase.
        """
        view = state.to_agent_view(color=self.color)
        phase_key = str(phase).split(".")[-1].lower()
        tools = _FUNCTIONS.get(phase_key, [])

        user_content = (
            f"Game state:\n{json.dumps(view, indent=2)}\n\n"
            f"Phase: {phase_key.upper()}. Troops to place: {state.troops_to_place}."
        )

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            tools=tools,  # type: ignore[arg-type]
            tool_choice="required" if tools else "none",
        )

        message = response.choices[0].message
        if message.tool_calls:
            call = message.tool_calls[0]
            tool_name = call.function.name
            inputs = json.loads(call.function.arguments or "{}")
            return self._map_tool(tool_name=tool_name, inputs=inputs, phase_key=phase_key)

        return {"action": "end_attacks"} if phase_key == "attack" else {"action": "skip"}

    def get_messages(self, state: GameState, inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return chat messages by calling the OpenAI API.

        Args:
            state: Current game state.
            inbox: Recent messages from other players.

        Returns:
            List of message dicts with a 'text' key, or empty list to pass.
        """
        view = state.to_agent_view(color=self.color)
        recent_chat = "\n".join(f"[{entry['from']}]: {entry['text']}" for entry in view["chat_log"][-5:])
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=100,
                messages=[
                    {"role": "system", "content": "You are playing Risk. Respond briefly or stay silent."},
                    {
                        "role": "user",
                        "content": f"Recent chat:\n{recent_chat}\n\nSay something strategic or reply PASS.",
                    },
                ],
            )
            text = (response.choices[0].message.content or "").strip()
            if text and text.upper() != "PASS":
                return [{"text": text[:200]}]
        except Exception:
            pass
        return []

    def _map_tool(self, tool_name: str, inputs: dict[str, Any], phase_key: str) -> dict[str, Any]:
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
        return {"action": "skip"}
