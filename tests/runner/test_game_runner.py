import random
from collections import Counter

from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
from risk_bench.game.actions import PlaceArmies
from risk_bench.game.events import ArmiesPlaced, MissionCompleted, TurnLimitReached
from risk_bench.game.types import Phase, PlayerId
from risk_bench.players.base import Decision, DecisionRequest, NegotiationTurn, Player, PlayerView
from risk_bench.players.random import RandomPlayer
from risk_bench.runner.game_runner import GameRecord, GameRunner


def random_players(player_ids: list[PlayerId], seed: int) -> dict[PlayerId, Player]:
    return {player_id: RandomPlayer(player_id=player_id, rng=random.Random(seed)) for player_id in player_ids}


def run_random_game(seed: int, max_turns: int) -> GameRecord:
    players = random_players(player_ids=["alice", "bob", "carla"], seed=seed)
    return talking_runner(players=players, seed=seed, max_turns=max_turns, allow_private_messages=True).run()


def talking_runner(
    players: dict[PlayerId, Player], seed: int, max_turns: int, allow_private_messages: bool
) -> GameRunner:
    return GameRunner(
        players=players,
        seed=seed,
        max_turns=max_turns,
        allow_private_messages=allow_private_messages,
        max_negotiation_rounds=3,
        journal=None,
        verbose=False,
    )


def test_a_full_random_game_finishes_with_a_winner_and_a_complete_record():
    record = run_random_game(seed=11, max_turns=2_000)

    assert record.winner in ("alice", "bob", "carla")
    assert record.player_ids == ("alice", "bob", "carla")
    assert set(record.missions) == {"alice", "bob", "carla"}
    assert any(isinstance(event, MissionCompleted) for event in record.events)
    assert record.turn_count >= 1
    assert record.invalid_action_counts == {"alice": 0, "bob": 0, "carla": 0}
    assert record.chat == ()  # random players never negotiate


def test_games_are_reproducible_from_the_seed():
    first = run_random_game(seed=23, max_turns=2_000)
    second = run_random_game(seed=23, max_turns=2_000)

    assert first.winner == second.winner
    assert first.turn_count == second.turn_count
    assert first.events == second.events
    assert first.missions == second.missions


def test_hitting_the_turn_limit_records_a_draw():
    record = run_random_game(seed=11, max_turns=1)

    assert record.winner is None
    assert any(isinstance(event, TurnLimitReached) for event in record.events)


class ScriptedTalker:
    """Plays randomly but says a fixed line each negotiation opportunity; records its views."""

    def __init__(self, player_id: PlayerId, rng: random.Random) -> None:
        self.player_id = player_id
        self.invalid_action_count = 0
        self.seen_views: list[PlayerView] = []
        self.negotiation_views: list[PlayerView] = []
        self._random_player = RandomPlayer(player_id=player_id, rng=rng)

    def decide_action(self, request: DecisionRequest) -> Decision:
        self.seen_views.append(request.view)
        return self._random_player.decide_action(request)

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        self.seen_views.append(view)
        self.negotiation_views.append(view)
        greeting = ChatMessage(
            turn_number=view.turn_number,
            sender=self.player_id,
            visibility=MessageVisibility.PUBLIC,
            recipient=None,
            text=f"{self.player_id} greets everyone on turn {view.turn_number}.",
        )
        return NegotiationTurn(messages=(greeting,), thought=None)


def test_negotiation_messages_reach_other_players_views():
    talkers = {
        player_id: ScriptedTalker(player_id=player_id, rng=random.Random(3)) for player_id in ("alice", "bob", "carla")
    }
    record = talking_runner(players=dict(talkers), seed=3, max_turns=5, allow_private_messages=True).run()

    assert len(record.chat) > 0
    alice_decision_views = [view for view in talkers["alice"].seen_views if view.visible_chat]
    assert any(message.sender == "bob" for view in alice_decision_views for message in view.visible_chat)


class SilentPlayer(ScriptedTalker):
    """Records negotiation opportunities like its parent but never says anything."""

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        self.negotiation_views.append(view)
        return NegotiationTurn(messages=(), thought=None)


def test_negotiation_runs_until_the_round_cap_while_everyone_keeps_talking():
    talkers = {
        player_id: ScriptedTalker(player_id=player_id, rng=random.Random(3)) for player_id in ("alice", "bob", "carla")
    }
    talking_runner(players=dict(talkers), seed=3, max_turns=2, allow_private_messages=True).run()

    for talker in talkers.values():
        opportunities_per_turn = Counter(view.turn_number for view in talker.negotiation_views)
        assert all(count == 3 for count in opportunities_per_turn.values())


def test_a_silent_pass_ends_the_negotiation_round():
    players = {
        player_id: SilentPlayer(player_id=player_id, rng=random.Random(3)) for player_id in ("alice", "bob", "carla")
    }
    talking_runner(players=dict(players), seed=3, max_turns=2, allow_private_messages=True).run()

    for player in players.values():
        opportunities_per_turn = Counter(view.turn_number for view in player.negotiation_views)
        assert all(count == 1 for count in opportunities_per_turn.values())


def test_replies_become_visible_within_the_same_turns_negotiation_round():
    talkers = {
        player_id: ScriptedTalker(player_id=player_id, rng=random.Random(3)) for player_id in ("alice", "bob", "carla")
    }
    talking_runner(players=dict(talkers), seed=3, max_turns=2, allow_private_messages=True).run()

    alice = talkers["alice"]
    assert any(
        message.turn_number == view.turn_number and message.sender != "alice"
        for view in alice.negotiation_views
        for message in view.visible_chat
    )


class SplittingPlacer(ScriptedTalker):
    """Splits every multi-army placement across two territories in a single decision; never chats."""

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        return NegotiationTurn(messages=(), thought=None)

    def decide_action(self, request: DecisionRequest) -> Decision:
        decision = super().decide_action(request)
        action = decision.actions[0]
        if not isinstance(action, PlaceArmies) or action.count < 2:
            return decision
        placements = [placement for placement in request.legal_actions if isinstance(placement, PlaceArmies)]
        other = next((placement for placement in placements if placement.territory != action.territory), action)
        split = action.count // 2
        batch = (
            PlaceArmies(territory=action.territory, count=action.count - split),
            PlaceArmies(territory=other.territory, count=split),
        )
        return Decision(actions=batch, chat_message=None, thought=None)


def test_batched_placements_apply_in_order_and_the_game_still_completes():
    players = {
        player_id: SplittingPlacer(player_id=player_id, rng=random.Random(13))
        for player_id in ("alice", "bob", "carla")
    }
    record = talking_runner(players=dict(players), seed=13, max_turns=2_000, allow_private_messages=False).run()

    assert record.winner in ("alice", "bob", "carla")
    assert record.invalid_action_counts == {"alice": 0, "bob": 0, "carla": 0}


class TableTalker(ScriptedTalker):
    """Stays silent in negotiation but attaches one message to every action it takes."""

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        self.negotiation_views.append(view)
        return NegotiationTurn(messages=(), thought=None)

    def decide_action(self, request: DecisionRequest) -> Decision:
        decision = super().decide_action(request)
        announcement = ChatMessage(
            turn_number=request.view.turn_number,
            sender=self.player_id,
            visibility=MessageVisibility.PUBLIC,
            recipient=None,
            text=f"{self.player_id} announces a {request.view.phase} move.",
        )
        return Decision(actions=decision.actions, chat_message=announcement, thought=None)


def test_table_talk_attached_to_actions_reaches_the_transcript_and_other_players():
    players = {
        player_id: TableTalker(player_id=player_id, rng=random.Random(3)) for player_id in ("alice", "bob", "carla")
    }
    record = talking_runner(players=dict(players), seed=3, max_turns=2, allow_private_messages=False).run()

    assert any("announces" in message.text for message in record.chat)
    bob_views = players["bob"].seen_views
    assert any(
        message.sender == "alice" and "announces" in message.text
        for view in bob_views
        for message in view.visible_chat
    )


def test_dropped_private_messages_do_not_keep_the_negotiation_round_alive():
    players = whisperers(seed=7)
    talking_runner(players=dict(players), seed=7, max_turns=2, allow_private_messages=False).run()

    for player in players.values():
        assert isinstance(player, PrivateWhisperer)
        opportunities_per_turn = Counter(view.turn_number for view in player.negotiation_views)
        assert all(count == 1 for count in opportunities_per_turn.values())


def test_setup_placements_of_others_are_not_delivered_as_events():
    talkers = {
        player_id: ScriptedTalker(player_id=player_id, rng=random.Random(5)) for player_id in ("alice", "bob", "carla")
    }
    talking_runner(players=dict(talkers), seed=5, max_turns=3, allow_private_messages=True).run()

    for player_id, talker in talkers.items():
        for view in talker.seen_views:
            setup_placements = [
                event
                for event in view.recent_events
                if isinstance(event, ArmiesPlaced) and _happened_during_setup(view=view, event=event)
            ]
            assert all(event.player_id == player_id for event in setup_placements)


def _happened_during_setup(view: PlayerView, event: ArmiesPlaced) -> bool:
    # before turn 1 every ArmiesPlaced event is a setup placement
    return view.turn_number == 0 or (view.turn_number == 1 and view.phase is Phase.REINFORCE)


class PrivateWhisperer(ScriptedTalker):
    """Sends one private message to a fixed recipient each negotiation opportunity."""

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        self.negotiation_views.append(view)
        recipient = next(info.player_id for info in view.players if info.player_id != self.player_id)
        whisper = ChatMessage(
            turn_number=view.turn_number,
            sender=self.player_id,
            visibility=MessageVisibility.PRIVATE,
            recipient=recipient,
            text="Psst. Truce?",
        )
        return NegotiationTurn(messages=(whisper,), thought="hoping for a truce")


def whisperers(seed: int) -> dict[PlayerId, Player]:
    return {
        player_id: PrivateWhisperer(player_id=player_id, rng=random.Random(seed))
        for player_id in ("alice", "bob", "carla")
    }


def test_private_messages_are_dropped_when_not_allowed():
    record = talking_runner(players=whisperers(seed=7), seed=7, max_turns=3, allow_private_messages=False).run()

    assert record.chat == ()


def test_private_messages_are_kept_when_allowed():
    record = talking_runner(players=whisperers(seed=7), seed=7, max_turns=3, allow_private_messages=True).run()

    assert len(record.chat) > 0
    assert all(message.visibility is MessageVisibility.PRIVATE for message in record.chat)


def test_views_expose_whether_private_messages_are_allowed():
    players = whisperers(seed=9)
    talking_runner(players=players, seed=9, max_turns=2, allow_private_messages=False).run()

    alice = players["alice"]
    assert isinstance(alice, PrivateWhisperer)
    assert all(not view.private_messages_allowed for view in alice.seen_views)
