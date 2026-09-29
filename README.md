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

The Git tag `v2` preserves the game engine, evaluator, and reader for the existing
experiments. Its default output directory is `data/v2/`, including game journals,
model traces, evaluations, and analysis. These experiment versions are separate
from the schema version numbers inside individual files.

To open the v2 application alongside ongoing development, use a separate checkout
and point it at the existing dataset:

```bash
git worktree add --detach ../risk-bench-v2 v2
cd ../risk-bench-v2
uv sync
OUTPUT_DIRECTORY=../risk-bench/data/v2 uv run python -m risk_bench.main serve --port 8421
```

V3 uses fresh games in `data/v3/`. Existing v2 games belong to the v2 application;
v3 rejects them even if copied into the new folder. It does not migrate, resume,
or fork v2 games. The revised commitment codebook is instrument version 2 within
experiment version 3; these numbers describe different things.

## Example games

These completed v2 games are included in the repository. Open them with the v2 application above.
Browsing their replays and saved evaluations requires no API keys.

| Game | Players | Saved evaluation |
| --- | --- | --- |
| [20260908-092609-seed0](data/v2/games/20260908-092609-seed0) | Gemini 3.1 Pro Preview, Claude Opus 5, GPT-5.6 Sol | Yes |
| [20260909-123257-seed1](data/v2/games/20260909-123257-seed1) | Human, Claude Opus 5, GPT-5.6 Sol | Yes |
| [20260910-092103-seed7](data/v2/games/20260910-092103-seed7) | Human, Claude Fable 5, GPT-6 Astra | Yes |
| [20260911-071339-seed8](data/v2/games/20260911-071339-seed8) | Human, Claude Sonnet 5, GPT-5.6 Terra, Gemini 3.8 Flash | No |

Each includes its game journal and model-call trace, plus evaluation and analysis files where available.

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

## Commitment analysis (v3)

The [two-tag codebook](docs/codebook-v3.md) gives each assessable passage an independent
statement and/or commitment record. A statement compares one proposition with a stated plan
or recorded fact; a commitment follows one actor's proposed or established undertaking through
revisions and outcomes. Each coder screens delivered messages, then cites exact trace passages
or structured board fields for its assessments. Deception requires a contradiction and explicit
private intent recorded before or in the same decision as the message.

The reader offers Timeline, Research tags, and Compare views. Click citations for the
source trace and board position, open an entire assessment window, and mark individual research
units reviewed. Evaluation reruns retain their own reviews, API traces, and analysis artifacts.

Reports keep coder counts separate. Proposals that never became promises remain distinct.
Adherence includes only valid established fulfilled or violated commitments with agent-choice
provenance. Citation issues are flagged for review without changing behavioral statuses.
Historical seven-code evaluations remain available under their original definitions.
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

Web games can start a live research evaluator at launch or from the game's evaluation panel.
It reviews each completed round in one call, keeping a ledger of all commitments (including
closed ones), and places a short research note and cited assessments on the timeline. The game
waits at the round boundary until the evaluator finishes. A failed call pauses the game there;
resuming retries the same round without replacing its moves. Once the result is recorded, it runs a final pass over the
ledger to review outcomes that depend on the game ending. The annotation toggle starts off
while a human is playing.

Render a finished game into a standalone HTML replay:

```bash
uv run python -m risk_bench.main view data/v3/games/<game-folder>/game.jsonl
```

## Status

RiskBench is under active development as a master's thesis (diplomityö). The core game engine, multi-provider agents, tournament/Elo scoring, and dashboard are in place; evaluation methodology and results are ongoing work.
