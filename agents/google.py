"""Gemini adapter using google-generativeai SDK."""

from __future__ import annotations

import json
from typing import Any

import google.generativeai as genai

from agents.base import BaseAgent
from game.state import GameState, Phase

_SYSTEM_PROMPT = """You are playing the board game Risk. Win by completing your secret mission.
- REINFORCE: Place all troops on owned territories (or redeem cards first).
- ATTACK: Attack adjacent enemy territories with 1-3 armies. End when done.
- FORTIFY: Move troops once between connected owned territories, or skip.
Respond ONLY with valid JSON matching the requested schema."""


def _build_prompt(view: dict[str, Any], phase_key: str, troops: int) -> str:
    schemas: dict[str, str] = {
        "reinforce": '{"territory": "<name>", "count": N} OR {"action": "redeem_cards", "cards": ["c1", "c2", "c3"]}',
        "attack": '{"from": "<name>", "to": "<name>", "attackers": <1-3>} OR {"action": "end_attacks"}',
        "fortify": '{"from": "<name>", "to": "<name>", "count": <int>} OR {"action": "skip"}',
    }
    schema = schemas.get(phase_key, '{"action": "skip"}')
    return (
        f"Game state:\n{json.dumps(view, indent=2)}\n\n"
        f"Phase: {phase_key.upper()}. Troops to place: {troops}.\n"
        f"Respond with JSON only, matching schema: {schema}"
    )


class GeminiAgent(BaseAgent):
    def __init__(
        self,
        color: str,
        name: str,
        model: str = "gemini-1.5-flash",
        api_key: str | None = None,
        max_tokens: int = 512,
    ) -> None:
        super().__init__(color, name)
        self.model_name = model
        self.max_tokens = max_tokens
        if api_key:
            genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name=model,
            system_instruction=_SYSTEM_PROMPT,
        )

    def get_move(self, state: GameState, phase: Phase) -> dict[str, Any]:
        view = state.to_agent_view(self.color)
        phase_key = str(phase).split(".")[-1].lower()
        prompt = _build_prompt(view, phase_key, state.troops_to_place)

        response = self._model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=self.max_tokens,
                temperature=0.7,
            ),
        )

        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"action": "end_attacks"} if phase_key == "attack" else {"action": "skip"}

        return self._normalize(data, phase_key)

    def _normalize(self, data: dict[str, Any], phase_key: str) -> dict[str, Any]:
        action = data.get("action", "")
        if action in ("end_attacks", "skip", "redeem_cards"):
            return data
        if phase_key == "reinforce" and "territory" in data:
            return data
        if phase_key == "attack" and "from" in data:
            return data
        if phase_key == "fortify" and "from" in data:
            return data
        return {"action": "end_attacks"} if phase_key == "attack" else {"action": "skip"}

    def get_messages(self, state: GameState, inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
        view = state.to_agent_view(self.color)
        recent = "\n".join(f"[{e['from']}]: {e['text']}" for e in view["chat_log"][-5:])
        try:
            response = self._model.generate_content(
                f"Recent Risk chat:\n{recent}\n\nReply briefly to influence the game, or reply PASS to stay silent.",
                generation_config=genai.types.GenerationConfig(max_output_tokens=100, temperature=0.9),
            )
            text = (response.text or "").strip()
            if text and text.upper() != "PASS":
                return [{"text": text[:200]}]
        except Exception:
            pass
        return []
