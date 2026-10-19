# Forking and faithful resume

New games use journal schema 4. Resume restores the committed execution position:
notes, visible turn history, negotiation pass/speaker (including silence), note writer,
pending responses and messages, batched actions, combat rounds, RNG positions, and
invalid-action/provider-failure counters. Credentials are read from the current environment;
model IDs, reasoning/token limits, retry settings, and prompt/tool sources are recorded.
Resume rejects changed prompt/tool sources rather than silently changing the experiment.
Use the recorded code version to continue an older experimental configuration.

In the web reader, every committed execution boundary is a seam between two trace
entries. Hovering it (or tabbing to it) reveals **Fork from here**, whose tooltip names
the operation the cut precedes, including silent negotiation opportunities and individual
note updates. Several entries can come out of one transition, so a seam appears only where
the cursor changes. Pressing it copies the prefix into a new branch that starts **paused**
and opens it; the parent keeps running. Finished games can be forked at earlier nonterminal
cursors. The branch header records the exact parent cursor, committed-prefix SHA-256, root
ancestry, and interventions. Inherited trace entries are labelled in the reader.

A game can start with context already in place: the launch form has one shared box every player
is given, plus a per-seat box behind the 📝 button on each row (`common_context` and
`player_context` on `POST /api/games`, the latter aligned with `players`). The shared line is
folded in above the per-seat one, so each player sees a single block. For a from-the-start
comparison, two games on the same seed differing only in that block are independent observations;
a fork is the tool for the other question, where the history must be shared.

A paused branch shows a setup card with one editable context field per player. The text is
rendered into that player's prompts as an ordinary game-information section, "Additional
information available to you:", next to the board, the chat and this turn's events — not as
an instruction and not as the player's own recollection, because the framing is part of the
treatment. **Preview next prompts** renders every living player's next prompt with and
without the edit and highlights the exact span that changes; it makes no provider call and
identifies the next request by advancing the real runner over the restored checkpoint.
**Save & resume game** commits the edit and starts the branch's worker.

A branch **adds to** what a player already knows rather than replacing it: the inherited text is
shown above the box, and what you write is appended beneath it. `POST /api/games/{id}/context`
takes `append` for this; `append: false` replaces outright, which is how a precise correction is
made.

A cursor such as `42:before` means the state immediately before transition 42.
`42:after` includes that transition. `0:after` is the initially dealt board before any
player request. A combat-round cursor includes ownership transfer, required troop
advance, elimination/card transfer, and terminal checks. Remaining rounds retain the
original attack parameters. A board step alone is not an execution cursor.

CLI examples (the fork is initially paused):

```sh
uv run python -m risk_bench.main fork data/games/parent/game.jsonl \
  --cursor 42:before --output data/games/branch/game.jsonl
uv run python -m risk_bench.main game --resume data/games/branch/game.jsonl
```

`--overrides overrides.json` accepts `context`, `instructions` and `controllers` dictionaries
keyed by the existing player identity, plus an `action` or `message` in journal JSON format.
`context` is the information section described above; `instructions` appends to the system
prompt instead and is the blunter of the two.
A controller change retains the seat, mission, hand, history, and pending work.
`--redecide` explicitly discards pending decision/negotiation work and records what was
discarded. Already delivered messages and applied actions remain in history.
Action/message replacements must be valid at the chosen pending operation and are
recorded as human interventions. Context and instructions affect the next model
request; they do not alter a response that has already been generated. Players receive fresh
requests rather than a transcript of earlier ones, so a midgame edit measures the effect of
introducing that information at that point, not of supplying it from the first turn.

The corresponding API is `POST /api/games/{id}/fork` with a required `cursor` and optional
`context`, `instructions`, `controllers`, `redecide`, `action`, `message`, `continuation_seed`,
and `start` (default `true`; the reader sends `false`). `POST /api/games/{id}/context` edits a
paused game's per-player context by appending a `context_intervention` transition next to an
`intervention` record — the journal is never rewritten — and `POST /api/games/{id}/prompt-preview`
returns each living player's next prompt before and after a proposed edit. Cursor lists and the
stored context are included in `GET /api/games/{id}`. Branches
have their own journal, trace, worker, mailbox, and result. They copy no evaluation
sidecars or future parent observations. Forks of forks preserve their ancestry.

**Inherit randomness** copies the engine and every player/fallback RNG position.
**New continuation randomness** (`--continuation-seed N`) changes future RNG streams
using the recorded `continuation-v1` derivation; it preserves the board, hands,
missions, and current draw/discard order. Deck reshuffles use the restored engine
stream. Neither mode makes fresh model responses deterministic. Recorded replay
makes no provider calls; identical deterministic players or future recorded responses
are needed to reproduce an entire continuation. Divergent actions can consume random
numbers at different positions.

Persistent provider failures pause the current request. Resume retries that operation
with the same knowledge and, for an interrupted decision retry, the same correction
conversation (including provider signatures), retry index, and fallback RNG. A successful
provider response resets the failure streak. Completed responses are committed before
their effects, so resume does not regenerate a committed batch or redeliver its messages.
An in-flight call without a committed response may be reissued, potentially incurring
another provider charge. Retry attempts inside a decision are not separate historical
cursors; an explicit provider pause preserves their continuation state.

Transactions are newline-terminated JSON records, flushed and fsynced. They include
both effects and the next execution checkpoint. Most checkpoints are JSON replacement
deltas against the immediately preceding checkpoint; every 256th is complete. Readers
ignore a torn final transaction, reject interior corruption, and reconstruct a single
committed prefix. Resume repairs only the incomplete tail before appending. Checkpoint
restoration uses allowlisted data types, never pickle or executable saved code.

Schema 1–3 journals remain viewable and replay with their original rules and RNG
semantics. Ordinary legacy resume is explicitly labelled a reconstructed scenario:
recorded notes/messages/turn events are recovered, but silent opportunities, pending
batches, retry state, and player RNG consumption were not recorded. Exact historical
forks of these missing cursors are rejected with an explanation. The last negotiated
turn is assumed complete. No later notes or provider observations are imported.

Branch analysis exposes ancestry, inherited/continuation span counts, and marks the
branch as a dependent observation. Usage/cost belongs to the branch's own trace.
Full-history annotations retain inherited evidence for context; use ancestry and
cursor identity to deduplicate shared prefixes in cross-game analysis. Branches are
created outside tournament manifests, so they do not automatically enter independent
game aggregates.

Validation uses deterministic players to compare every recorded cursor against the
uninterrupted future requests, events, notes, messages, and random stream positions,
plus API outage recovery and independent API-launched branches.
