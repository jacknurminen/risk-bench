"""Preview the prompts a paused game will render next, with and without a context edit.

An experimental intervention is only meaningful if the exact text the player receives is
visible beforehand, so the preview renders through the same prompt code the game runs and
never contacts a provider.
"""

import random
from dataclasses import dataclass, replace
from pathlib import Path

from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
from risk_bench.game.actions import Action
from risk_bench.players.base import Decision, DecisionRequest, NegotiationTurn, PlayerView
from risk_bench.players.rendering import (
    render_decision_prompt,
    render_negotiation_prompt,
    render_noting_prompt,
    render_system_prompt,
)
from risk_bench.players.view import build_player_view
from risk_bench.runner.game_runner import GameRunner
from risk_bench.runner.journal import RestoredGame, restore_game, restored_context


@dataclass(frozen=True)
class PromptPreview:
    """One player's next prompt, before and after a proposed context edit.

    ``is_next_request`` marks the single player the runner will actually ask next; the
    others are shown the negotiation prompt they would receive from the current position,
    which the game may reach a few transitions later.
    """

    player: str
    request_kind: str  # 'decision', 'negotiation' or 'noting'
    is_next_request: bool
    system_prompt: str
    before: str
    after: str


def preview_next_prompts(journal_path: Path, additional_context: dict[str, str]) -> list[PromptPreview]:
    """Render every living player's next prompt with the stored and the proposed context.

    Args:
        journal_path: The paused game's journal.
        additional_context: The proposed per-player context text, keyed by player identity;
            omitted players keep whatever the game already stores for them.

    Returns:
        One preview per living player, in seat order.

    Raises:
        ValueError: If the journal predates execution checkpoints, or the game has finished
            and will render no further prompt.
    """
    restored = restore_game(journal_path)
    if restored.checkpoint is None:
        raise ValueError("Prompt previews need a committed execution checkpoint; this journal has none.")
    if restored.is_finished:
        raise ValueError("This game has finished; there is no next prompt to preview.")
    stored = restored.checkpoint.get("additional_context") or {}
    next_request = _capture_next_request(restored=restored)
    views = _negotiation_views(restored=restored)
    if next_request is not None:
        views[next_request.view.player_id] = next_request.view
    asked_next = next_request.view.player_id if next_request is not None else None
    previews = []
    for name in restored.header.player_specs:
        if name not in views:
            continue
        exact = next_request if name == asked_next else None
        kind = exact.kind if exact is not None else "negotiation"
        legal_actions = exact.legal_actions if exact is not None else []
        previews.append(
            PromptPreview(
                player=name,
                request_kind=kind,
                is_next_request=exact is not None,
                system_prompt=render_system_prompt(view=views[name]),
                before=_render(view=views[name], kind=kind, legal_actions=legal_actions, text=stored.get(name)),
                after=_render(
                    view=views[name],
                    kind=kind,
                    legal_actions=legal_actions,
                    text=additional_context.get(name, stored.get(name)) or None,
                ),
            )
        )
    return previews


@dataclass(frozen=True)
class _NextRequest:
    kind: str
    view: PlayerView
    legal_actions: list[Action]


class _RequestCaptured(Exception):
    def __init__(self, request: _NextRequest) -> None:
        super().__init__("captured the next player request")
        self.request = request


class _CapturingPlayer:
    # Stands in for every controller while the runner advances to its next request, so the
    # preview learns who is asked and with which prompt without duplicating the turn routing.
    def __init__(self, rng: random.Random) -> None:
        self.invalid_action_count = 0
        self._rng = rng

    def decide_action(self, request: DecisionRequest) -> Decision:
        raise _RequestCaptured(_NextRequest("decision", request.view, request.legal_actions))

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        raise _RequestCaptured(_NextRequest("negotiation", view, []))

    def update_notes(self, view: PlayerView) -> str | None:
        raise _RequestCaptured(_NextRequest("noting", view, []))

    def restore_checkpoint_state(self, state: object) -> None:
        return  # a preview answers nothing, so a controller's own saved state cannot affect it


def _capture_next_request(restored: RestoredGame) -> _NextRequest | None:
    header = restored.header
    runner = GameRunner(
        players={name: _CapturingPlayer(rng=random.Random()) for name in header.player_specs},
        additional_context={},
        seed=header.seed,
        max_turns=header.max_turns,
        allow_private_messages=header.allow_private_messages,
        max_negotiation_rounds=header.max_negotiation_rounds,
        commentators=(),
        max_concurrent_commentators=1,
        journal=None,
        trace=None,
        verbose=False,
    )
    try:
        runner.resume(restored=restored)
    except _RequestCaptured as captured:
        return captured.request
    return None  # the remaining work finishes the game without asking anyone again


def _negotiation_views(restored: RestoredGame) -> dict[str, PlayerView]:
    context = restored_context(restored)
    state = restored.state
    return {
        name: build_player_view(
            state=state,
            player_id=name,
            visible_chat=_visible_messages(messages=restored.messages, player_id=name, turn=state.turn_number),
            recent_events=tuple(context["_current_turn_events"].get(name, [])),
            private_messages_allowed=restored.header.allow_private_messages,
            notes=context["_notes"].get(name),
            additional_context=None,
        )
        for name, player in state.players.items()
        if player.eliminated_by is None
    }


def _visible_messages(messages: list[ChatMessage], player_id: str, turn: int) -> tuple[ChatMessage, ...]:
    return tuple(
        message
        for message in messages
        if message.turn_number == turn
        and (message.visibility is MessageVisibility.PUBLIC or player_id in (message.sender, message.recipient))
    )


def _render(view: PlayerView, kind: str, legal_actions: list[Action], text: str | None) -> str:
    view = replace(view, additional_context=text)
    if kind == "decision":
        return render_decision_prompt(request=DecisionRequest(view=view, legal_actions=legal_actions))
    if kind == "noting":
        return render_noting_prompt(view=view)
    return render_negotiation_prompt(view=view)
