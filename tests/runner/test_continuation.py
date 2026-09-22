"""Compare actual future requests and effects, not just final board snapshots."""

import copy
import json
import random
from dataclasses import replace

import pytest

from risk_bench.agents.base import Response
from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
from risk_bench.config import Settings
from risk_bench.game.actions import Attack, EndAttackPhase, PlaceArmies, SkipFortify
from risk_bench.launch import persisted_configuration, resume_game
from risk_bench.players.base import Decision, NegotiationTurn
from risk_bench.players.llm import LLMPlayer, ProviderUnavailableError
from risk_bench.players.random import RandomPlayer
from risk_bench.runner import checkpoint
from risk_bench.runner.forking import apply_additional_context, fork_game
from risk_bench.runner.game_runner import GameRunner
from risk_bench.runner.journal import JournalHeader, JournalWriter, raw_journal, read_journal, restore_game
from risk_bench.viewer.prompts import preview_next_prompts
from risk_bench.viewer.snapshots import build_view_model

NAMES = ("alice", "bob", "carla")


class ScriptedPlayer(RandomPlayer):
    def __init__(self, name):
        super().__init__(name, random.Random(name))
        self.requests = []
        self.speaking = 0
        self.attacks = 0

    def checkpoint_state(self):
        return {"speaking": self.speaking, "attacks": self.attacks}

    def restore_checkpoint_state(self, data):
        self.speaking, self.attacks = data["speaking"], data["attacks"]

    def decide_action(self, request):
        self.requests.append(("decision", request))
        placements = [a for a in request.legal_actions if isinstance(a, PlaceArmies)]
        if placements:
            action = self._rng.choice(placements)
            actions = (
                (replace(action, count=1), replace(action, count=action.count - 1)) if action.count > 1 else (action,)
            )
            return Decision(actions, None, "a private plan")
        attacks = [a for a in request.legal_actions if isinstance(a, Attack)]
        if attacks and self.attacks < 2:
            self.attacks += 1
            return Decision((replace(self._rng.choice(attacks), max_attacks=4),), None, None)
        action = next(a for a in request.legal_actions if isinstance(a, (EndAttackPhase, SkipFortify)))
        return Decision((action,), None, None)

    def compose_messages(self, view):
        self.requests.append(("negotiation", view))
        self.speaking += 1
        if self.speaking % 2 == 0:
            return NegotiationTurn((), None)
        message = ChatMessage(view.turn_number, self.player_id, MessageVisibility.PUBLIC, None, "Our pact stands.")
        private = ChatMessage(
            view.turn_number,
            self.player_id,
            MessageVisibility.PRIVATE,
            NAMES[(NAMES.index(self.player_id) + 1) % 3],
            "Keep our border safe.",
        )
        return NegotiationTurn((message, private), "Trust, but verify.")

    def update_notes(self, view):
        self.requests.append(("note", view))
        return f"{self.player_id}: pact at {view.turn_number}; prior: {view.notes}"


def players():
    return {name: ScriptedPlayer(name) for name in NAMES}


class ObservedRunner(GameRunner):
    def _commit(self, state, rng, stage):
        self.final_engine = (checkpoint.encode(state), rng.getstate())
        super()._commit(state, rng, stage)


def runner(group, journal=None, turns=2):
    return ObservedRunner(group, {}, 19, turns, True, 3, (), 1, journal, None, False)


@pytest.fixture
def completed(tmp_path, monkeypatch):
    # fsync itself is not under test in the exhaustive continuation matrix.
    monkeypatch.setattr("risk_bench.runner.journal.os.fsync", lambda fd: None)
    path = tmp_path / "parent" / "game.jsonl"
    writer = JournalWriter(path, False)
    writer.write_header(JournalHeader(4, 19, dict.fromkeys(NAMES, "random"), 2, True, 3, []))
    group = players()
    record = runner(group, writer).run()
    writer.close()
    return path, record, group


def test_every_committed_cursor_preserves_requests_and_continuation(completed):
    path, expected_record, original = completed
    transitions = raw_journal(path)[1:]
    final = restore_game(path)
    stages = {t["stage"] for t in transitions}
    assert {
        "setup",
        "negotiation_request",
        "negotiation_delivery",
        "action",
        "note_request",
        "note_apply",
        "decision_request",
        "decision_delivery",
        "commentary",
        "result",
    } <= stages
    saw_pending_round = saw_batch = False
    for transition in transitions[:-1]:
        restored = restore_game(path, f"{transition['id']}:after")
        assert restored.checkpoint is not None
        p = restored.checkpoint["progress"]
        saw_pending_round |= p["rounds_left"] is not None
        saw_batch |= p["stage"] == "action" and p["action_index"] == 1
        group = players()
        resumed = runner(group)
        result = resumed.resume(restored)
        assert resumed.final_engine == (checkpoint.encode(final.state), final.rng.getstate())
        assert result == expected_record, (transition["id"], p)
        for name, player in group.items():
            if player.requests:
                assert player.requests == original[name].requests[-len(player.requests) :], (transition["id"], name)
            assert player._rng.getstate() == original[name]._rng.getstate()
    assert saw_pending_round and saw_batch


def test_forks_isolate_history_rng_and_preserve_ancestry(completed, tmp_path):
    path, _, _ = completed
    transition = next(t for t in raw_journal(path)[1:] if t["stage"] == "negotiation_request")
    cursor = f"{transition['id']}:before"
    child = tmp_path / "child" / "game.jsonl"
    sibling = tmp_path / "sibling" / "game.jsonl"
    fork_game(path, child, cursor)
    fork_game(path, sibling, cursor, continuation_seed=77, instructions={"alice": "Defend the pact."})
    parent = restore_game(path, cursor)
    inherited, fresh = restore_game(child), restore_game(sibling)
    assert inherited.checkpoint is not None and fresh.checkpoint is not None and parent.checkpoint is not None
    assert checkpoint.encode(inherited.state) == checkpoint.encode(fresh.state) == checkpoint.encode(parent.state)
    assert inherited.rng.getstate() == parent.rng.getstate() != fresh.rng.getstate()
    assert inherited.state.deck._rng is inherited.rng
    assert inherited.messages == parent.messages
    assert fresh.checkpoint["players"]["alice"]["additional_instructions"] == "Defend the pact."
    assert inherited.checkpoint["progress"] == parent.checkpoint["progress"]
    grandchild = tmp_path / "grandchild" / "game.jsonl"
    fork_game(child, grandchild, f"{inherited.checkpoint['sequence']}:after")
    ancestry = restore_game(grandchild).header.ancestry
    assert ancestry["root_game_id"] == "parent"
    assert ancestry["ancestors"] == ["parent", "child"]
    assert not (grandchild.parent / "game.trace.jsonl").exists()
    inherited.state.armies.clear()
    assert restore_game(child).state.armies
    assert restore_game(path).is_finished


def test_torn_transition_and_resume_append_repair(completed, tmp_path):
    path, expected, _ = completed
    data = path.read_bytes().splitlines(keepends=True)
    crash = tmp_path / "crash.jsonl"
    crash.write_bytes(b"".join(data[:-2]) + data[-2][: len(data[-2]) // 2])
    restored = restore_game(crash)
    writer = JournalWriter(crash, True)
    assert runner(players(), writer).resume(restored) == expected
    writer.close()
    assert restore_game(crash).is_finished
    assert read_journal(crash)[-1]["type"] == "result"


def test_legacy_cursor_is_rejected_and_notes_reconstructed(tmp_path):
    path = tmp_path / "old.jsonl"
    writer = JournalWriter(path, False)
    writer.write_header(JournalHeader(3, 19, dict.fromkeys(NAMES, "random"), 1, True, 1, []))
    writer.write_note("alice", 0, "Keep this promise")
    writer.close()
    with pytest.raises(ValueError, match="legacy"):
        restore_game(path, "12:after")
    restored = restore_game(path)
    assert restored.recovery_limitations
    group = players()
    runner(group, turns=1).resume(restored)
    assert group["alice"].requests[0][1].view.notes == "Keep this promise"


def test_pending_action_override_and_redecision(completed, tmp_path):
    path, _, _ = completed
    transition = next(t for t in raw_journal(path)[1:] if t["checkpoint"]["progress"]["stage"] == "action")
    cursor = f"{transition['id']}:after"
    child = tmp_path / "override" / "game.jsonl"
    from risk_bench.game.serialization import action_to_json

    pending = checkpoint.decode(transition["checkpoint"]["progress"]["pending"])
    fork_game(path, child, cursor, action=action_to_json(pending.actions[0]))
    assert restore_game(child).header.ancestry["interventions"]["origin"] == "human"
    redecision = tmp_path / "redecision" / "game.jsonl"
    fork_game(path, redecision, cursor, redecide=True)
    restored = restore_game(redecision)
    assert restored.checkpoint is not None
    assert restored.checkpoint["progress"]["stage"] == "decision_request"
    assert restored.header.ancestry["interventions"]["discarded_pending"] is not None


@pytest.mark.parametrize("kind", ["decision", "negotiation", "note"])
def test_api_failure_pauses_exact_operation_and_resumes(completed, tmp_path, kind):
    path, expected, original = completed
    stage = {"decision": "decision_request", "negotiation": "negotiation_request", "note": "note_request"}[kind]
    transition = next(
        t
        for t in raw_journal(path)[1:]
        if t["checkpoint"]["progress"]["stage"] == stage
        and (kind != "note" or t["checkpoint"]["progress"]["commented_turn"] > 0)
    )
    branch = tmp_path / kind / "game.jsonl"
    fork_game(path, branch, f"{transition['id']}:after")
    group = players()
    restored = restore_game(branch)
    assert restored.checkpoint is not None
    p = restored.checkpoint["progress"]
    actor = (
        restored.state.current_player
        if kind == "decision"
        else p["order"][p["speaker"]]
        if kind == "negotiation"
        else NAMES[p["note_index"]]
    )
    method = {"decision": "decide_action", "negotiation": "compose_messages", "note": "update_notes"}[kind]
    attempted = []

    def fail(request):
        attempted.append(request)
        raise ProviderUnavailableError("provider temporarily down")

    setattr(group[actor], method, fail)
    writer = JournalWriter(branch, True)
    with pytest.raises(ProviderUnavailableError):
        runner(group, writer).resume(restored)
    writer.close()
    paused = restore_game(branch)
    assert paused.checkpoint is not None
    assert paused.checkpoint["progress"] == p
    recovered = players()
    result = runner(recovered).resume(paused)
    assert result == expected
    assert recovered[actor].requests[0] == (kind, attempted[0])
    for name in NAMES:
        if recovered[name].requests:
            assert recovered[name].requests == original[name].requests[-len(recovered[name].requests) :]


def test_llm_retry_conversation_counters_and_fallback_rng_survive_pause():
    from tests.players.test_llm import INVALID_ATTACK_RESPONSE, attack_request

    class Outage:
        def __init__(self):
            self.calls = 0

        def respond(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return INVALID_ATTACK_RESPONSE
            raise RuntimeError("offline")

    class Recovered:
        def __init__(self):
            self.requests = []

        def respond(self, messages, tools):
            self.requests.append(copy.deepcopy(messages))
            return Response("No legal tool")

    first = LLMPlayer("alice", Outage(), 2, 1, random.Random(8))
    with pytest.raises(ProviderUnavailableError):
        first.decide_action(attack_request())
    data = json.loads(json.dumps(checkpoint.player_state(first)))
    recovered_agent = Recovered()
    restored = LLMPlayer("alice", recovered_agent, 99, 99, random.Random(99))
    checkpoint.restore_player(restored, data)
    expected_agent = Recovered()
    first._agent = expected_agent
    assert first.decide_action(attack_request()) == restored.decide_action(attack_request())
    assert expected_agent.requests == recovered_agent.requests
    assert any(m.role == "tool" for m in recovered_agent.requests[0])
    assert restored.invalid_action_count == 1
    assert restored._consecutive_provider_failures == 0
    assert restored._rng.getstate() == first._rng.getstate()


def test_round_restoration_preserves_capture_elimination_and_card_transfer():
    from risk_bench.chat.diplomacy import ChatTranscript
    from risk_bench.game.cards import CardDesign, TerritoryCard
    from risk_bench.game.engine import apply_action
    from risk_bench.game.types import Territory
    from tests.game.test_engine import ATTACK_NORTHWEST, alaska_attack_state

    state = alaska_attack_state(12)
    state.armies[Territory.NORTHWEST_TERRITORY] = 3
    state.players["carla"].cards = [TerritoryCard(CardDesign.WILD, None)]
    attack = replace(ATTACK_NORTHWEST, max_attacks=10, armies_to_leave_behind=11)
    rng = random.Random(22)
    expected = apply_action(state, attack, random.Random(22), shared_rng=True)
    game = runner(players())
    game._reset_progress(ChatTranscript())
    game._sequence = 0
    game._progress = game._initial_progress(1)
    game._progress.update(stage="action", actor="alice", pending=checkpoint.encode(Decision((attack,), None, None)))
    count = 0
    while game._progress["action_index"] == 0:
        state = game._advance(state, rng)
        # Serialize/restore each round, including deck binding and all pending attack parameters.
        saved = json.loads(json.dumps(game._checkpoint(state, rng)))
        state, rng = checkpoint.restore_engine(saved)
        game._progress = saved["progress"]
        count += 1
    assert count > 1
    assert checkpoint.encode(state) == checkpoint.encode(expected.state)
    assert state.players["carla"].eliminated_by == "alice"
    assert state.players["carla"].cards == []
    assert state.players["alice"].cards
    assert state.armies[Territory.NORTHWEST_TERRITORY] >= 3
    assert game._progress["rounds_left"] is None


def test_restored_deck_reshuffles_use_the_engine_stream(completed):
    path, _, _ = completed
    original = restore_game(path, "0:after")
    assert original.checkpoint is not None
    restored = restore_game(path, "0:after")
    for game in (original, restored):
        game.state.deck._discard_pile = game.state.deck._draw_pile
        game.state.deck._draw_pile = []
    assert [original.state.deck.draw() for _ in range(44)] == [restored.state.deck.draw() for _ in range(44)]
    assert original.rng.getstate() == restored.rng.getstate()
    assert original.rng.getstate() != checkpoint.decode(original.checkpoint["rng"])


def test_retry_provider_signatures_are_lossless():
    from risk_bench.agents.base import Message, ToolCall

    message = Message("assistant", tool_calls=(ToolCall("id", "attack", {}, b"\x00opaque\xff"),))
    assert checkpoint.decode(json.loads(json.dumps(checkpoint.encode(message)))) == message


@pytest.mark.parametrize("stage", ["decision_request", "action", "negotiation_delivery", "note_apply"])
@pytest.mark.parametrize("committed", [False, True])
def test_crash_at_effect_and_response_commit_boundaries(completed, tmp_path, stage, committed):
    source, expected, _ = completed
    lines = source.read_bytes().splitlines(keepends=True)
    index = next(index for index, line in enumerate(lines) if json.loads(line).get("stage") == stage)
    # A syntactically complete JSON record without its newline is still uncommitted.
    tail = lines[index] if committed else lines[index].rstrip(b"\n")
    crash = tmp_path / "boundary.jsonl"
    crash.write_bytes(b"".join(lines[:index]) + tail)
    writer = JournalWriter(crash, True)
    result = runner(players(), writer).resume(restore_game(crash))
    writer.close()
    assert result == expected
    assert restore_game(crash).is_finished


def test_before_cursors_equal_the_preceding_after_cursor(completed):
    path, _, _ = completed
    for transition in raw_journal(path)[2:]:
        before = restore_game(path, f"{transition['id']}:before")
        after = restore_game(path, f"{transition['id'] - 1}:after")
        assert before.checkpoint == after.checkpoint
        assert before.messages == after.messages


def test_continuation_seed_replays_deterministically_and_keeps_prefix(completed, tmp_path):
    path, _, _ = completed
    siblings = [tmp_path / name / "game.jsonl" for name in ("first", "second")]
    cursor = next(t for t in raw_journal(path)[1:] if t["stage"] == "negotiation_request")
    for sibling in siblings:
        fork_game(path, sibling, f"{cursor['id']}:before", continuation_seed=788)
    first, second = [restore_game(sibling) for sibling in siblings]
    original = restore_game(path, f"{cursor['id']}:before")
    assert first.messages == second.messages == original.messages
    assert checkpoint.encode(first.state) == checkpoint.encode(original.state)
    a, b = runner(players()), runner(players())
    assert a.resume(first) == b.resume(second)
    assert a.final_engine == b.final_engine


def test_live_parent_growth_cannot_enter_a_selected_prefix(completed, tmp_path, monkeypatch):
    source, _, _ = completed
    from risk_bench.runner import forking

    original_reader = forking.raw_journal

    def read_then_grow(path):
        prefix = original_reader(path)
        with path.open("a") as stream:
            stream.write(
                json.dumps({"type": "note", "player": "alice", "turn": 999, "text": "Future private intelligence"})
                + "\n"
            )
        return prefix

    monkeypatch.setattr(forking, "raw_journal", read_then_grow)
    destination = tmp_path / "live-branch" / "game.jsonl"
    forking.fork_game(source, destination, "0:after")
    assert "Future private intelligence" not in destination.read_text()
    assert restore_game(destination).messages == []


def _views(player):
    return [request.view if kind == "decision" else request for kind, request in player.requests]


def test_player_context_only_reaches_later_prompts_and_survives_resume(completed, tmp_path):
    path, _, _ = completed
    transition = next(t for t in raw_journal(path)[1:] if t["stage"] == "negotiation_request")
    child = tmp_path / "briefed" / "game.jsonl"
    fork_game(path, child, f"{transition['id']}:before")

    plain = preview_next_prompts(journal_path=child, additional_context={})
    assert sum(preview.is_next_request for preview in plain) == 1
    proposed = preview_next_prompts(journal_path=child, additional_context={"bob": "alice broke the pact."})
    bob_proposal = next(preview for preview in proposed if preview.player == "bob")
    assert "alice broke the pact." in bob_proposal.after
    assert "alice broke the pact." not in bob_proposal.before

    apply_additional_context(journal=child, context={"alice": "carla is one territory from winning."}, append=False)
    briefed = preview_next_prompts(journal_path=child, additional_context={})
    alice_before = next(preview for preview in plain if preview.player == "alice")
    alice_after = next(preview for preview in briefed if preview.player == "alice")
    assert "carla is one territory from winning." in alice_after.before
    assert "Additional information available to you:" not in alice_before.before
    # the intervention is information inside the game context, never an instruction in the system prompt
    assert alice_before.system_prompt == alice_after.system_prompt
    assert "carla is one territory" not in next(p for p in briefed if p.player == "bob").before
    assert "carla is one territory" not in path.read_text()

    group = players()
    runner(group).resume(restore_game(child))
    assert all(view.additional_context == "carla is one territory from winning." for view in _views(group["alice"]))
    assert all(view.additional_context is None for view in _views(group["bob"]))
    resumed = restore_game(child)
    assert resumed.checkpoint is not None
    assert resumed.checkpoint["additional_context"]["alice"] == "carla is one territory from winning."


def test_context_edits_are_rejected_for_unknown_players_and_finished_games(completed, tmp_path):
    path, _, _ = completed
    with pytest.raises(ValueError, match="Unknown player"):
        apply_additional_context(journal=path, context={"dave": "hello"}, append=False)
    with pytest.raises(ValueError, match="finished"):
        apply_additional_context(journal=path, context={"alice": "hello"}, append=False)
    with pytest.raises(ValueError, match="finished"):
        preview_next_prompts(journal_path=path, additional_context={})


def test_edited_code_is_reported_and_recorded_rather_than_blocking_the_resume(tmp_path, monkeypatch):
    monkeypatch.setattr("risk_bench.runner.journal.os.fsync", lambda fd: None)
    path = tmp_path / "drifting" / "game.jsonl"
    writer = JournalWriter(path, False)
    recorded = persisted_configuration(Settings())
    writer.write_header(
        replace(JournalHeader(4, 19, dict.fromkeys(NAMES, "random"), 2, True, 3, []), configuration=recorded)
    )
    writer.close()

    # the journal now carries a configuration; pretend the prompts and engine moved on since
    moved_on = {
        **recorded,
        "code_version": "different",
        "prompt_and_tools": {**recorded["prompt_and_tools"], "rendering": "edited"},
    }
    monkeypatch.setattr("risk_bench.launch.persisted_configuration", lambda settings: moved_on)
    resume_game(journal_file=path, settings=Settings(), commentators=[], verbose=False)

    assert restore_game(path).is_finished
    changes = [record for record in read_journal(path) if record["type"] == "configuration_change"]
    assert len(changes) == 1
    assert any("rendering" in change for change in changes[0]["changes"])
    assert any("engine" in change for change in changes[0]["changes"])
    assert build_view_model(journal_path=path)["configuration_changes"] == changes[0]["changes"]


def test_context_given_at_launch_reaches_the_first_prompt_and_a_fork_adds_to_it(tmp_path, monkeypatch):
    monkeypatch.setattr("risk_bench.runner.journal.os.fsync", lambda fd: None)
    path = tmp_path / "briefed" / "game.jsonl"
    writer = JournalWriter(path, False)
    writer.write_header(JournalHeader(4, 19, dict.fromkeys(NAMES, "random"), 2, True, 3, []))
    group = players()
    ObservedRunner(group, {"alice": "The table has met before."}, 19, 2, True, 3, (), 1, writer, None, False).run()
    writer.close()

    # every request alice received carried it, and nobody else's did
    assert all(view.additional_context == "The table has met before." for view in _views(group["alice"]))
    assert all(view.additional_context is None for view in _views(group["bob"]))

    transition = next(t for t in raw_journal(path)[1:] if t["stage"] == "negotiation_request")
    child = tmp_path / "child" / "game.jsonl"
    fork_game(path, child, f"{transition['id']}:before")
    apply_additional_context(journal=child, context={"alice": "Carla just broke the pact."}, append=True)

    inherited = restore_game(child)
    assert inherited.checkpoint is not None
    # the fork adds to the briefing rather than replacing it: both halves reach the next prompt
    assert inherited.checkpoint["additional_context"]["alice"] == (
        "The table has met before.\n\nCarla just broke the pact."
    )
    alice = next(p for p in preview_next_prompts(journal_path=child, additional_context={}) if p.player == "alice")
    assert "The table has met before." in alice.before and "Carla just broke the pact." in alice.before


def test_concurrent_context_edits_preserve_both_updates(completed, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, BrokenBarrierError

    from risk_bench.runner import forking

    source, _, _ = completed
    branch = tmp_path / "concurrent" / "game.jsonl"
    fork_game(source, branch, "0:after")
    read = forking.raw_journal
    readers = Barrier(2)

    def overlapping_reads(path):
        records = read(path)
        # Without serialization both editors read the same sequence before either commits.
        # With serialization the first times out, commits, and the second sees that commit.
        try:
            readers.wait(timeout=0.2)
        except BrokenBarrierError:
            pass
        return records

    monkeypatch.setattr(forking, "raw_journal", overlapping_reads)
    with ThreadPoolExecutor(max_workers=2) as pool:
        edits = [pool.submit(apply_additional_context, branch, {"alice": text}, True) for text in ("one", "two")]
        for edit in edits:
            edit.result(timeout=5)

    restored = restore_game(branch)
    assert restored.checkpoint is not None
    assert restored.checkpoint["additional_context"]["alice"] in {"one\n\ntwo", "two\n\none"}
    assert [r["id"] for r in raw_journal(branch)[1:]] == list(range(4))


@pytest.mark.parametrize("edit", ["append", "replace", "clear", "fork"])
def test_retry_receives_previewed_context_edit_without_losing_corrections(tmp_path, monkeypatch, edit):
    from tests.players.test_llm import INVALID_ATTACK_RESPONSE

    monkeypatch.setattr("risk_bench.runner.journal.os.fsync", lambda fd: None)
    signed_call = replace(INVALID_ATTACK_RESPONSE.tool_calls[0], provider_data={"signature": b"\x00\xff"})

    class Outage:
        def __init__(self):
            self.calls = []

        def respond(self, messages, tools):
            self.calls.append(copy.deepcopy(messages))
            if len(self.calls) == 1:
                return replace(INVALID_ATTACK_RESPONSE, tool_calls=(signed_call,))
            raise RuntimeError("offline")

    class Capture:
        def __init__(self):
            self.calls = []

        def respond(self, messages, tools):
            self.calls.append(copy.deepcopy(messages))
            raise RuntimeError("stop after capturing the resumed request")

    source = tmp_path / "outage" / "game.jsonl"
    writer = JournalWriter(source, False)
    writer.write_header(JournalHeader(4, 19, dict.fromkeys(NAMES, "random"), 2, True, 3, []))
    group = {name: LLMPlayer(name, Outage(), 2, 1, random.Random(name)) for name in NAMES}
    with pytest.raises(ProviderUnavailableError):
        GameRunner(group, {"alice": "Original briefing."}, 19, 2, True, 3, (), 1, writer, None, False).run()
    writer.close()
    parent_bytes = source.read_bytes()
    original = restore_game(source)
    assert original.checkpoint is not None
    actor = original.state.current_player
    retry = checkpoint.decode(original.checkpoint["players"][actor]["_retry_conversation"])
    assert retry[2].tool_calls[0].provider_data == {"signature": b"\x00\xff"}
    assert original.checkpoint["players"][actor]["_retry_index"] == 1

    branch = tmp_path / "edited" / "game.jsonl"
    text = "" if edit == "clear" else "Protect the eastern border."
    fork_game(
        source, branch, f"{raw_journal(source)[-1]['id']}:after", context={actor: text} if edit == "fork" else {}
    )
    if edit != "fork":
        proposed = "Original briefing.\n\n" + text if edit == "append" else text
        preview = next(p for p in preview_next_prompts(branch, {actor: proposed}) if p.is_next_request)
        apply_additional_context(branch, {actor: text}, append=edit == "append")
    else:
        preview = next(p for p in preview_next_prompts(branch, {}) if p.is_next_request)

    capture = Capture()
    resumed = {name: LLMPlayer(name, capture, 99, 99, random.Random(name)) for name in NAMES}
    with pytest.raises(ProviderUnavailableError):
        runner(resumed).resume(restore_game(branch))
    sent = capture.calls[0]
    assert sent[1].content == preview.after
    assert sent[0] == retry[0] and sent[2:] == retry[2:]
    assert resumed[actor]._retry_index == 1
    assert resumed[actor]._rng.getstate() == group[actor]._rng.getstate()
    assert resumed[actor].invalid_action_count == group[actor].invalid_action_count
    assert source.read_bytes() == parent_bytes
