# RiskBench

**Benchmarking strategic reasoning, decision-making under uncertainty, and multi-agent negotiation in large language models through the game of Risk.**

RiskBench is a benchmark in which LLM agents play full games of [Risk](https://en.wikipedia.org/wiki/Risk_(game)) against one another. Agents are ranked by an Elo rating computed from head-to-head results across a tournament, giving a single comparable score per model.

It follows the tradition of task-grounded LLM benchmarks such as VendBench, StockBench, and PokerBench: rather than scoring isolated question/answer pairs, RiskBench measures competence inside a complete, adversarial environment with real consequences for every decision.

## Why Risk?

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

Between turns, a **reaction window** opens the group chat so every surviving agent can negotiate before play continues. The game ends when an agent completes its secret mission (or is the last one standing), and the result feeds the tournament's Elo rankings.

Each player implements a single interface (`risk_bench/players/base.py`): given the current `GameState` and `Phase`, return a move; optionally return chat messages given the conversation so far. If a model returns an invalid move, the engine retries a few times and then falls back to a random legal move, so a single bad response never crashes a game.

## Behavioural commentary

Beyond the single Elo score, each game can be watched by one or more **commentators** — LLMs that tag what players are doing turn by turn against a fixed vocabulary. Tagging is split across two independent axes so a model can be profiled as, say, *competent but treacherous* rather than collapsed onto one line:

- **Skill** — strong versus weak play (good odds, continent prioritisation, blunders, …).
- **Conduct** — cooperative, adversarial, or deceptive behaviour (alliances and kept promises versus betrayal, threats, and acted-on deception).

A third class of tags captures **affect** (tilt, taunting, …); these are descriptive and charted as frequency only, never summed into a score. Each axis runs as its own specialised commentator with a focused, criterion-referenced rubric, and the two can run on different model families — which weakens the self-enhancement bias of a model judging its own family. Commentary is stored per turn in the game journal and surfaced as cumulative per-axis charts in the viewer.

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

## Getting started

The project uses [`uv`](https://github.com/astral-sh/uv) and requires Python 3.13+.

```bash
uv sync
```

Players are given inline as specs — `random` or `provider:model` (providers `anthropic`/`openai`/`google`), e.g. `anthropic:claude-opus-5`. Any model id the provider accepts works; the registry in `risk_bench/agents/models.py` is what the setup form offers and what usage reports can price, not a whitelist. There is no separate registration step.

Run a quick game between random players to check the engine end-to-end:

```bash
uv run python -m risk_bench.main game random random random
```

Play a model-backed game, optionally with split commentators (a bare `--commentator <spec>` runs every rubric on one model):

```bash
uv run python -m risk_bench.main game anthropic:claude-opus-5 openai:gpt-5.6-sol google:gemini-3.8-flash \
    --commentator skill=anthropic:claude-opus-5 --commentator conduct=google:gemini-3.8-flash
```

Run a tournament and write its JSON report:

```bash
uv run python -m risk_bench.main tournament anthropic:claude-opus-5 openai:gpt-5.6-sol random --games 10
```

Evaluate a stored game with a panel of whole-game **evaluators** — models that read the full trace
offline (a finished or paused game) and emit cited per-turn tags and a per-player
synthesis into a re-runnable `game.eval.jsonl` sidecar:

```bash
uv run python -m risk_bench.main evaluate data/games/<game-folder>/game.jsonl \
    --evaluator anthropic:claude-opus-5 --evaluator google:gemini-3.1-pro-preview
```

Render a finished game into a standalone HTML replay:

```bash
uv run python -m risk_bench.main view data/games/<game-folder>/game.jsonl
```

Serve the web app to browse games, watch them live, and launch new ones:

```bash
uv run python -m risk_bench.main serve --port 8420
```

Provider API keys are read from the environment (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).

## Status

RiskBench is under active development as a master's thesis (diplomityö). The core game engine, multi-provider agents, tournament/Elo scoring, and dashboard are in place; evaluation methodology and results are ongoing work.