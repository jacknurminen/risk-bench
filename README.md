# RiskBench

**Benchmarking strategic reasoning, decision-making under uncertainty, and multi-agent negotiation in large language models through the game of Risk.**

RiskBench is a benchmark in which LLM agents play full games of [Risk](https://en.wikipedia.org/wiki/Risk_(game)) against one another. Agents are ranked by an Elo rating computed from head-to-head results across a tournament, giving a single comparable score per model.

## Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.13+.
From the repository root:

```bash
uv python install 3.13
uv sync
cp .env.example .env
```

Generate keys in the [Claude Console](https://platform.claude.com/settings/keys),
[OpenAI Platform](https://platform.openai.com/api-keys), or
[Google AI Studio](https://aistudio.google.com/apikey).
In `.env`, uncomment and set the keys for the providers you use:

```dotenv
ANTHROPIC_API_KEY=your-anthropic-api-key
OPENAI_API_KEY=your-openai-api-key
GOOGLE_API_KEY=your-google-api-key
```

Only selected providers need keys. Settings load from `.env` or environment variables;
`.env` is excluded from Git. Random players require no keys.

Start the web app:

```bash
uv run python -m risk_bench.main serve --port 8420
```

Open [http://localhost:8420](http://localhost:8420). V3 games are saved under `data/v3/games/`.

## Versioned datasets

The Git tag `v2` preserves the earlier game engine, evaluator, reader, and four
example games under `data/v2/`. Those examples are no longer tracked on this branch;
existing local copies remain usable. These experiment versions are separate from
the schema version numbers inside individual files.

To open the v2 application alongside ongoing development, use a separate checkout
with its saved example dataset:

```bash
git worktree add --detach ../risk-bench-v2 v2
cd ../risk-bench-v2
uv sync
uv run python -m risk_bench.main serve --port 8421
```

V3 uses fresh games in `data/v3/`. Existing v2 games belong to the v2 application;
v3 rejects them even if copied into the new folder. It does not migrate, resume,
or fork v2 games. The thesis commitment/deception codebook is instrument version 4 within
experiment version 3; these numbers describe different things.

## Included tournament

The repository includes [20261012-101836-seed8349](data/v3/games/20261012-101836-seed8349),
a completed 12-game tournament between Claude Opus 5.5, Gemini 3.8 Flash, and GPT-6.1 Sol,
all configured with medium reasoning effort. Seating cycles through all six orders twice.
Open the current application and select the tournament to browse its games and aggregated
statistics. Browsing saved replays and evaluations requires no API keys.

The dataset includes the tournament manifest, saved launch settings, journals, model-call
traces, evaluations, analyses, and review records. Three superseded game attempts and an
earlier evaluation version remain available for audit; the manifest selects the 12 current
attempts used in tournament statistics. Runtime logs and other local experiments are ignored.

## Why Risk?

It follows the tradition of task-grounded LLM benchmarks such as VendBench, StockBench, and PokerBench: rather than scoring isolated question/answer pairs, RiskBench measures competence inside a complete, adversarial environment with real consequences for every decision.

Most game-based benchmarks isolate a single skill. Poker probes probabilistic reasoning and bluffing; trading benchmarks probe sequential decision-making under market uncertainty. Risk is interesting precisely because it combines several demands in one environment:

- **Long-horizon strategic planning** — territory control, continent bonuses, and turn-by-turn army economics reward multi-turn plans over greedy play.
- **Decision-making under uncertainty** — combat is resolved by dice, so agents must reason about expected outcomes and risk, not certainties.
- **Multi-agent negotiation and social reasoning** — between actions, agents share a group chat where they can form alliances, threaten, bluff, and coordinate against a leader. There is no fixed opponent; the political situation is created by the players.

A strong RiskBench agent therefore has to plan, gamble well, *and* manage relationships with other agents — making the benchmark a compact testbed for the kind of open-ended strategic agency that single-skill benchmarks do not capture.

## How it works

Each game runs the standard Risk turn structure to completion:

1. **Setup** — territories and starting armies are distributed across the 42-territory board.
2. **Reinforce** — the active player receives and places armies, optionally redeeming Risk cards.
3. **Attack** — the player declares attacks; combat is resolved with dice, and conquering a territory may eliminate an opponent.
4. **Fortify** — the player moves armies between two adjacent territories to consolidate.

A **turn** belongs to one player. A **round** is one pass through the fixed seating order,
skipping eliminated players; round 1 begins after setup. The reader and player prompts show
both, for example **Round 3 · Turn 8**. Turn numbers remain continuous and turn limits still
count individual turns. A round advances when the seating order wraps, even if its original
first player has been eliminated. Round tracking is preserved by execution checkpoints during resume and forks.

Before each turn, a **negotiation session** opens the group chat so every surviving agent can
negotiate before play continues. Prompts encourage explicit commitment deadlines such as
“until the end of round 4” or “until the start of Bob's next turn.” These agreements remain
unenforced. The game ends when an agent completes its secret mission (or is the last one
standing), and the result feeds the tournament's Elo rankings.

Each player implements a single interface (`risk_bench/players/base.py`): given the current `GameState` and `Phase`, return a move; optionally return chat messages given the conversation so far. Invalid moves receive a bounded correction exchange. Exhaustion pauses the game with that exchange preserved. Any provider error pauses immediately, including negotiation and note-writing. Resume retries the pending operation with its player context intact. No random move or implicit silence is substituted; random players remain an explicitly selected baseline.

LLM game actions and public-message tools require a `private_memo` string argument (1–1200 characters).
`say_nothing` and private messages need no memo. The signature exposes the requirement; plain response text is optional
and cannot replace the argument. The memo and every tool call are validated before
any action is executed or message is delivered. Invalid responses enter the same bounded retry
flow; the successful response's memo is journaled with its decision ID. Memos are recorded
decision summaries, not access to hidden provider reasoning. Human and random-baseline controllers
retain their existing input interfaces.

## Commitments and documented deception (instrument v4)

The [thesis-aligned codebook](docs/codebook-v4.md) records commitments and documented deception
in communication that establishes, explains, revises or denies commitments. Generic contradiction
and sincerity tags are excluded. A deception incident requires a delivered proposition,
contradiction evidence, and explicit prior or same-decision private evidence of deliberate
misrepresentation. Multiple evidence bases remain one incident. Uncertain candidates retain a
separate review history and do not count as findings.

Commitments use `proposed`, `agreed`, `fulfilled`, `violated`, `unresolved` and `closed`.
An explicit unilateral promise starts `agreed`; acceptance is required only by its terms.
Participant roles, responses, revisions and status histories retain their evidence. Existing
commitment updates append to saved records. Communication promises can be fulfilled by matching
later delivered messages; board outcomes still require applied actions or complete observation
windows. Final open promises are unresolved with a reason.

The reader offers Timeline, Research tags, and Compare views. Click citations for the
source trace and board position, open an entire assessment window, and mark individual research
units reviewed. Evaluation reruns retain their own reviews, API traces, and analysis artifacts.

Reports keep coder counts separate. Proposals that never became promises remain distinct.
Adherence includes only valid established fulfilled or violated commitments with agent-choice
provenance. Citation issues are flagged for review without changing behavioral statuses.
Evaluation completion and annotation review flags are separate: a completed evaluation may
contain excluded annotations needing review. Failed calls and unfinished observation cutoffs
remain distinguishable. Historical [two-tag v3](docs/codebook-v3.md) and seven-code evaluations
retain their original definitions; fresh evaluations use sidecar schema 6. A codebook change
archives the prior live ledger and reassesses from evidence instead of relabelling old findings.
There is no overall honesty score. Forks retain ancestry and shared history must not
be treated as independent observations. See [v3 implementation notes](docs/v3.md).

## Project layout

| Area | Path | Responsibility |
| --- | --- | --- |
| Game rules | `risk_bench/game/` | Board, state, combat, cards, turn engine, move validation |
| Negotiation | `risk_bench/chat/` | Between-turn diplomacy: the group-chat reaction window |
| Players | `risk_bench/players/` | Player interface (`base.py`), the LLM player, and a `RandomPlayer` baseline |
| Provider clients | `risk_bench/agents/` | Low-level model clients (Anthropic, OpenAI, Google, xAI) plus call tracing |
| Game loop | `risk_bench/runner/` | Drives a single game, writes the JSONL journal, and runs the commentators |
| Tournaments | `risk_bench/tournament/` | Multi-game runner, Elo rating, and report writing |
| Web API | `risk_bench/api/` | FastAPI app: browse games, watch them live, and launch new ones |
| Viewer | `risk_bench/viewer/` + `ui/` | Self-contained HTML replays and stats, plus the templates and board map |
| Wiring | `risk_bench/{config,specs,launch}.py` | Settings, spec parsing, and game launching |
| CLI | `risk_bench/main.py` | Entry point: the `game`, `tournament`, `evaluate`, `view`, and `serve` commands |

## Command-line usage

The browser is enough to play. These commands are for running agent-only games, tournaments,
evaluations, and replay exports directly from the terminal, after completing the setup above.

Players are given inline as specs — `random` or `provider:model` (providers `anthropic`/`openai`/`google`), e.g. `anthropic:claude-opus-5-5`. Any model id the provider accepts works; the registry in `risk_bench/agents/models.py` is what the setup form offers and what usage reports can price, not a whitelist. There is no separate registration step.

Run a quick game between random players to check the engine end-to-end:

```bash
uv run python -m risk_bench.main game random random random
```

Play a model-backed game:

```bash
uv run python -m risk_bench.main game anthropic:claude-opus-5-5 openai:gpt-6-sol google:gemini-3.8-flash
```

Run a tournament and write its JSON report:

```bash
uv run python -m risk_bench.main tournament anthropic:claude-opus-5-5 openai:gpt-6-sol random --games 10
```

Evaluate a stored game with a panel of whole-game **evaluators** — models that read the full trace
offline (a finished or paused game) and emit cited research units into a versioned
`game.eval.jsonl` sidecar:

```bash
uv run python -m risk_bench.main evaluate data/v3/games/<game-folder>/game.jsonl \
    --evaluator anthropic:claude-opus-5-5 --evaluator google:gemini-3.1-pro-preview
```

Whole-game evaluation starts from the original trace without importing live annotations.
Each evaluator returns a complete assessment and screening coverage in one call. Citation,
participant and chronology errors are returned for up to two full-response corrections before
saving. Remaining review flags stay visible; malformed or incomplete responses mark the run
failed. Prior evaluations are archived for comparison. This differs from replaying rounds,
which retains an incremental ledger within the new run.

Web setup has a **Random / Cycle** seating toggle. Random uses a seed-derived shuffle for
each game. Cycle runs through all seat permutations in roster order, repeating as needed.
With three players and 12 games, each of the six orders runs twice and each player occupies
each seat four times. The resolved schedule is saved before launch and survives queue
cancellation and restart. Missions and starting territories remain generated by the game seed;
private context stays attached to its player. The API setting is `seating_mode: "random" | "cycle"`.

Web setup separates **total games** from **parallel games**. For example, total games `10`
and parallel games `2` queues ten games and runs at most two workers at once. A slot includes
the game's automatic evaluation; when the worker finishes or pauses, the next queued game starts.
Queued games appear in the game list and can be cancelled before they start. Resuming a game
also respects its tournament's limit. Stopping the server cancels the in-memory queue and pauses
running games; saved launch settings let you start waiting games again from the list.

On a tournament's stats page, **Continue tournament** adds more games to the same tournament.
It reuses the saved launch setup, continues with the next seeds and seating-cycle positions,
and keeps the existing parallel-game limit. New and existing games contribute to the same
aggregate stats. Previous evaluation versions retain their original game coverage; continuation
switches the report to **Latest per game** to include the additions.

**Replay a tournament game** restarts one paused or finished game from the beginning, using
its seed, actual seat order and saved setup. The replacement occupies the same tournament slot:
the game count, seed sequence and seating cycle do not advance, and current aggregate stats count
only the replacement attempt. Original journals, traces and evaluations remain accessible under
**Previous game attempts**. Replays use the current code and prompts, so fixes apply to the new run.
Pause a running game (or cancel its queue entry) before replaying it; active evaluations must finish first.

Game and tournament setup have one evaluator configuration with a **Live / Whole game** toggle.
On a finished tournament's stats page, **Configure tournament evaluations** reruns all games
with a selected evaluator panel and Whole game or Replay rounds method. It uses the tournament's
parallel-game limit; a failed game does not stop the remaining evaluations. Each bulk run is a
saved tournament version, with per-game progress, failures and review-flag counts. The version
selector also retains the collection from before the first bulk rerun. Selecting a saved version
pins each game's evaluation and evidence links, even if individual games are rerun later.
**Latest per game** follows individual updates. Interrupted batches remain visible after a server
restart; start another rerun to create a new version. No paid rerun starts until the run button is clicked.
Choose the model and reasoning effort once, then select the method; choose **Off** to skip
automatic evaluation. Live evaluation reviews rounds during play. Whole-game analysis runs
automatically after the game finishes.

Web games can start a live research evaluator at launch or from the game's evaluation panel.
It reviews each completed round in one call, keeping a ledger of all commitments (including
closed ones), and places a short research note and cited assessments on the timeline. The game
waits at the round boundary until the evaluator finishes. A failed call pauses the game there;
resuming retries the same round without replacing its moves. Once the result is recorded, it runs a final pass over the
ledger to review outcomes that depend on the game ending. The annotation toggle starts off
while a human is playing.

While a game is paused or after it finishes, **Replay rounds** in the evaluation bar starts a fresh annotation
ledger using the current evaluator instructions and your selected model and effort. It uses
the same round-by-round calls as live play, without changing the game journal. Replay stops
after the last completed round; an unfinished round is deferred, and the final review only
runs once the game ends. Resume play after replay finishes to continue the new annotation ledger.
Earlier annotations, evaluator traces, and human reviews remain accessible through the version
selector. If a call fails, **Resume evaluation** retries from the last completed round;
**Replay rounds** instead starts another version from the beginning.

After a game finishes, **Configure evaluations** offers the same **Live / Whole game** toggle.
Switch methods while keeping your selected panel, then choose **Replay rounds** or
**Run whole-game analysis**. Each run selects one method. Version labels identify the method,
and tournament research statistics use the latest run of each method in separate groups.
Whole-game analysis can make one focused citation-repair call if a response refers to missing
catalog entries. Repairs must supply exact source evidence and pass the usual validation.
If a later correction fails, the last structurally complete assessment is retained with its
review flags and a correction note. Runs with no complete assessment still fail.

Render a finished game into a standalone HTML replay:

```bash
uv run python -m risk_bench.main view data/v3/games/<game-folder>/game.jsonl
```

## Status

RiskBench is under active development as a master's thesis (diplomityö). The core game engine, multi-provider agents, tournament/Elo scoring, and dashboard are in place; evaluation methodology and results are ongoing work.
