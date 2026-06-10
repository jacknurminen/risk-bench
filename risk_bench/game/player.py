from __future__ import annotations

import json
import random
import re
from typing import Any, Protocol, runtime_checkable

from agents.base import Agent, Message, Tool, ToolCall
from game.actions import Attack, EndAttacks, Fortify, PlaceTroops, Skip, _Action
from game.board import BOARD, Territory
from game.state import ChatEntry, GameState

_SYSTEM_PROMPT = """You are a player in a game of Risk. Win by completing your secret mission, \
or by being the last player left on the board.

You take turns made up of three phases:
- Reinforce: place new troops on territories you own (and optionally redeem card sets for more).
- Attack: attack adjacent enemy territories to conquer the board, then end the phase.
- Fortify: optionally move troops once between two connected territories you own.

On each decision you must call exactly one of the tools you are given. Think strategically about \
continent bonuses, defensible borders, and your mission."""

_CHAT_PROMPT = """You are a player in a game of Risk talking to the other players. Keep messages \
short and strategic. You may negotiate, bluff, threaten, or stay silent. Reply with a single brief \
message, or reply with nothing to stay silent."""


@runtime_checkable
class Player(Protocol):
    """A participant that chooses actions for whoever's turn it is.

    The orchestrator drives a single agent loop: it offers the actions legal in
    the current phase (from ``Game.actions_for``) and asks the player to act. An
    illegal or unparseable move is not special-cased; the orchestrator simply
    feeds the error back through ``previous_error`` and asks again. ``react`` lets
    the player optionally speak during chat windows.
    """

    def act(
        self, state: GameState, actions: tuple[type[_Action], ...], previous_error: str | None
    ) -> _Action | None: ...

    def react(self, state: GameState, inbox: list[ChatEntry]) -> list[str]: ...


class LLMPlayer:
    """A player driven by an LLM agent over a single, continuous conversation.

    The agent sees one running thread for the whole game. Each observation — the
    board state after its last move, or the error explaining why that move was
    rejected — is delivered as the result of its previous tool call, so an illegal
    move is just another thing the agent learns and responds to.
    """

    def __init__(self, agent: Agent, color: str) -> None:
        self._agent = agent
        self._color = color
        self._messages: list[Message] = [Message(role="system", content=_SYSTEM_PROMPT)]
        self._pending_calls: tuple[ToolCall, ...] = ()

    def act(self, state: GameState, actions: tuple[type[_Action], ...], previous_error: str | None) -> _Action | None:
        """Advance the conversation by one turn and return the action the agent took.

        Args:
            state: Current game state.
            actions: The action models the player may choose from this phase.
            previous_error: If set, the reason the player's last move was rejected;
                otherwise the player is acting on a fresh situation.

        Returns:
            The action parsed from the agent's tool call, or None if the agent did
            not call exactly one recognized tool (the orchestrator treats this as a
            failed attempt and asks again).
        """
        observation = previous_error if previous_error is not None else self._render(state=state)
        self._deliver(observation=observation)

        response = self._agent.respond(messages=self._messages, tools=[_to_tool(model=model) for model in actions])
        self._messages.append(Message(role="assistant", content=response.text, tool_calls=response.tool_calls))
        self._pending_calls = response.tool_calls

        if len(response.tool_calls) != 1:
            return None
        call = response.tool_calls[0]
        for model in actions:
            if _tool_name(model=model) == call.name:
                return model.model_validate(call.arguments)
        return None

    def react(self, state: GameState, inbox: list[ChatEntry]) -> list[str]:
        """Optionally produce a chat message in response to recent table talk."""
        view = state.to_agent_view(color=self._color)
        recent = "\n".join(f"[{entry['from']}]: {entry['text']}" for entry in view["chat_log"][-8:])
        user = (
            f"You are playing as {self._color}.\n\nRecent table talk:\n{recent or '(silence)'}\n\n"
            "Say something to the other players, or stay silent."
        )
        response = self._agent.respond(
            messages=[Message(role="system", content=_CHAT_PROMPT), Message(role="user", content=user)],
            tools=[],
        )
        text = (response.text or "").strip()
        return [text] if text else []

    def _deliver(self, observation: str) -> None:
        if not self._pending_calls:
            self._messages.append(Message(role="user", content=observation))
            return
        # Every tool call the model made must be answered. The first carries the real
        # observation; any extras are rejected so the model learns to act one at a time.
        for index, call in enumerate(self._pending_calls):
            content = observation if index == 0 else "Ignored: make exactly one move at a time."
            self._messages.append(Message(role="tool", content=content, tool_call_id=call.id))
        self._pending_calls = ()

    def _render(self, state: GameState) -> str:
        view = state.to_agent_view(color=self._color)
        return (
            f"Current game state:\n{json.dumps(view, indent=2)}\n\n"
            f"You are playing as {self._color}. It is the {state.phase} phase. "
            f"Troops to place: {state.troops_to_place}.\n\n"
            "Call exactly one of the available tools to make your move."
        )


class RandomPlayer:
    """A baseline player that picks a legal action uniformly at random and never chats."""

    def __init__(self, color: str) -> None:
        self._color = color

    def act(self, state: GameState, actions: tuple[type[_Action], ...], previous_error: str | None) -> _Action | None:
        """Pick a legal action at random for whichever phase the actions belong to.

        It always returns a legal move, so ``previous_error`` is never set in practice.
        """
        if PlaceTroops in actions:
            return self._reinforce(state=state)
        if Attack in actions:
            return self._attack(state=state)
        return self._fortify(state=state)

    def react(self, state: GameState, inbox: list[ChatEntry]) -> list[str]:
        """Stay silent."""
        return []

    def _reinforce(self, state: GameState) -> PlaceTroops:
        territory = random.choice(state.territories_of(player=self._color))
        return PlaceTroops(territory=territory, count=state.troops_to_place)

    def _attack(self, state: GameState) -> Attack | EndAttacks:
        if random.random() < 0.3:
            return EndAttacks()
        targets = self._attackable(state=state)
        if not targets:
            return EndAttacks()
        source, target = random.choice(targets)
        return Attack(source=source, target=target, armies=min(3, state.territories[source].troops - 1))

    def _fortify(self, state: GameState) -> Fortify | Skip:
        if random.random() < 0.5:
            return Skip()
        pairs = self._fortifiable(state=state)
        if not pairs:
            return Skip()
        source, target = random.choice(pairs)
        return Fortify(source=source, target=target, count=random.randint(1, state.territories[source].troops - 1))

    def _attackable(self, state: GameState) -> list[tuple[Territory, Territory]]:
        targets: list[tuple[Territory, Territory]] = []
        for source, territory_state in state.territories.items():
            if territory_state.owner != self._color or territory_state.troops < 2:
                continue
            for neighbor in BOARD.territories[source].adjacent:
                if state.territories[neighbor].owner != self._color:
                    targets.append((source, neighbor))
        return targets

    def _fortifiable(self, state: GameState) -> list[tuple[Territory, Territory]]:
        pairs: list[tuple[Territory, Territory]] = []
        for source, territory_state in state.territories.items():
            if territory_state.owner != self._color or territory_state.troops < 2:
                continue
            for neighbor in BOARD.territories[source].adjacent:
                if state.territories[neighbor].owner == self._color:
                    pairs.append((source, neighbor))
        return pairs


# --- Action <-> tool projection ---


def _to_tool(model: type[_Action]) -> Tool:
    schema = _inline_defs(schema=model.model_json_schema())
    schema.pop("title", None)
    return Tool(name=_tool_name(model=model), description=(model.__doc__ or "").strip(), parameters=schema)


def _tool_name(model: type[_Action]) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", model.__name__).lower()


def _inline_defs(schema: dict[str, Any]) -> dict[str, Any]:
    definitions = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                referenced = definitions[node["$ref"].split("/")[-1]]
                return resolve(dict(referenced))
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)
