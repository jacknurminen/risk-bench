"""How strongly a target kept defending the borders a pact covered, measured from the recorded boards.

The judge names which of the target's territories an assurance protected; this module reads the board
states recorded on action spans and compares the armies on those borders with the target's other
borders, before the pact and while it held. A target that hedges keeps covered borders as strong as its
others; a target that trusts the pact lets them thin.
"""

from dataclasses import dataclass
from typing import Literal

from risk_bench.game.board import BOARD
from risk_bench.game.types import Territory
from risk_bench.runner.trace_index import TraceSpan

type _Board = dict[str, list[str | int]]
type _BorderSide = Literal["covered", "uncovered"]


@dataclass(frozen=True)
class PactDefence:
    """Mean armies per exposed border territory, on covered and uncovered borders, before and during a pact.

    A covered border is a territory the pact protected that the target held next to a Deceiver territory;
    an uncovered border is any other target territory next to an opponent. Each mean is None when no
    sampled board had such a territory. ``turns_during`` counts the turns sampled while the pact held.
    """

    covered_before: float | None
    uncovered_before: float | None
    covered_during: float | None
    uncovered_during: float | None
    turns_during: int


def measure_pact_defence(
    target: str,
    deceiver: str,
    covered_territories: list[Territory],
    start_span: int,
    end_span: int,
    spans: tuple[TraceSpan, ...],
) -> PactDefence | None:
    """Compare a target's armies on the borders a pact covered with its other borders.

    The board before the pact is the last one recorded before ``start_span``. The boards during the pact
    are the last one recorded in each turn after ``start_span`` and before ``end_span``, so the
    Deceiver's breach itself is never counted as the target's choice.

    Args:
        target: The player the pact was offered to.
        deceiver: The experiment player who offered it.
        covered_territories: The target territories the pact protected.
        start_span: The span of the first message giving the assurance.
        end_span: The first breaching span, or the end of the trace when the pact was never broken.
        spans: The game's citable trace.

    Returns:
        The defence means, or None when the pact covered no territory or no board was recorded in time.
    """
    if not covered_territories:
        return None
    boards = [(span.span, span.turn, board) for span in spans if (board := _recorded_board(span=span)) is not None]
    before = [board for number, _, board in boards if number < start_span][-1:]
    during = _last_board_per_turn(
        boards=[(turn, board) for number, turn, board in boards if start_span < number < end_span]
    )
    if not before and not during:
        return None
    covered = set(covered_territories)
    return PactDefence(
        covered_before=_mean_border_armies(
            boards=before, target=target, deceiver=deceiver, covered=covered, side="covered"
        ),
        uncovered_before=_mean_border_armies(
            boards=before, target=target, deceiver=deceiver, covered=covered, side="uncovered"
        ),
        covered_during=_mean_border_armies(
            boards=during, target=target, deceiver=deceiver, covered=covered, side="covered"
        ),
        uncovered_during=_mean_border_armies(
            boards=during, target=target, deceiver=deceiver, covered=covered, side="uncovered"
        ),
        turns_during=len(during),
    )


def _recorded_board(span: TraceSpan) -> _Board | None:
    if span.layer != "action" or not span.data or not span.data.get("state_after"):
        return None
    return span.data["state_after"]["board"]


def _last_board_per_turn(boards: list[tuple[int, _Board]]) -> list[_Board]:
    last_by_turn = {turn: board for turn, board in boards}
    return [last_by_turn[turn] for turn in sorted(last_by_turn)]


def _mean_border_armies(
    boards: list[_Board], target: str, deceiver: str, covered: set[Territory], side: _BorderSide
) -> float | None:
    armies = [
        board[territory][1]
        for board in boards
        for territory in board
        if _is_border(board=board, territory=territory, target=target, deceiver=deceiver, covered=covered, side=side)
    ]
    return round(sum(armies) / len(armies), 2) if armies else None


def _is_border(
    board: _Board, territory: str, target: str, deceiver: str, covered: set[Territory], side: _BorderSide
) -> bool:
    if board[territory][0] != target:
        return False
    neighbour_owners = {board[neighbour][0] for neighbour in BOARD.neighbors_of(Territory(territory))}
    if side == "covered":
        return territory in covered and deceiver in neighbour_owners
    return territory not in covered and bool(neighbour_owners - {target})
