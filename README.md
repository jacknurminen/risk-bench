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
4. **Fortify** — the player moves armies between connected territories to consolidate.

Between turns, a **reaction window** opens the group chat so every surviving agent can negotiate before play continues. The game ends when one agent controls the entire board, and the result feeds the tournament's Elo rankings.

Each agent implements a single interface (`agents/base.py`): given the current `GameState` and `Phase`, return a move; optionally return chat messages given the conversation so far. If a model returns an invalid move, the engine retries a few times and then falls back to a random legal move, so a single bad response never crashes a game.

## Project layout

| Area | Path | Responsibility |
| --- | --- | --- |
| Game rules | `game/` | Board, state, combat, cards, turn engine, group chat, move validation |
| Agents | `agents/` | `BaseAgent` interface plus provider implementations (Anthropic, OpenAI, Google, xAI) and a `RandomAgent` baseline |
| Tournaments | `tournament/` | Multi-game runner and Elo rating |
| Storage | `storage/` | JSON persistence for agents, games, and the leaderboard |
| Web | `web/` | FastAPI dashboard for the leaderboard, game replays, and agent profiles |
| CLI | `main.py` | Entry point for registering agents and running tournaments |

## Getting started

The project uses [`uv`](https://github.com/astral-sh/uv) and requires Python 3.13+.

```bash
uv sync
```

Run a quick game between random agents to check the engine end-to-end:

```bash
uv run python main.py tournament quick --players 3
```

Register a model-backed agent and run a tournament:

```bash
uv run python main.py agent add --name "claude" --provider anthropic --model claude-sonnet-4-6
uv run python main.py tournament run --agents id1,id2,id3 --games 10
```

Serve the dashboard to browse results and replays:

```bash
uv run python main.py serve --port 8000
```

Provider API keys are read from the environment (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).

## Status

RiskBench is under active development as a master's thesis (diplomityö). The core game engine, multi-provider agents, tournament/Elo scoring, and dashboard are in place; evaluation methodology and results are ongoing work.