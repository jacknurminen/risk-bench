# Game mechanics and project structure

[Overview](../README.md) · [Usage](usage.md) · [Research](research.md) · [Architecture](architecture.md)

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
standing). Tournaments record outcomes and descriptive statistics.

Each player implements a single interface (`risk_bench/players/base.py`): given the current `GameState` and `Phase`, return a move; optionally return chat messages given the conversation so far. Invalid moves receive a bounded correction exchange. Exhaustion pauses the game with that exchange preserved. Any provider error pauses immediately, including negotiation and note-writing. Resume retries the pending operation with its player context intact. No random move or implicit silence is substituted; random players remain an explicitly selected baseline.

LLM game actions and public-message tools require a `private_memo` string argument (1–1200 characters).
`say_nothing` and private messages need no memo. The signature exposes the requirement; plain response text is optional
and cannot replace the argument. The memo and every tool call are validated before
any action is executed or message is delivered. Invalid responses enter the same bounded retry
flow; the successful response's memo is journaled with its decision ID. Memos are recorded
decision summaries, not access to hidden provider reasoning. Human and random-baseline controllers
retain their existing input interfaces.

## Project layout

| Area | Path | Responsibility |
| --- | --- | --- |
| Game rules | `risk_bench/game/` | Board, state, combat, cards, turn engine, move validation |
| Negotiation | `risk_bench/chat/` | Between-turn diplomacy: the group-chat reaction window |
| Players | `risk_bench/players/` | Player interface (`base.py`), the LLM player, and a `RandomPlayer` baseline |
| Provider clients | `risk_bench/agents/` | Low-level model clients (Anthropic, OpenAI, Google, xAI) plus call tracing |
| Game loop | `risk_bench/runner/` | Drives a single game, writes the JSONL journal, and runs the commentators |
| Tournaments | `risk_bench/tournament/` | Multi-game runner, outcomes, and report writing |
| Web API | `risk_bench/api/` | FastAPI app: browse games, watch them live, and launch new ones |
| Viewer | `risk_bench/viewer/` + `ui/` | Self-contained HTML replays and stats, plus the templates and board map |
| Wiring | `risk_bench/{config,specs,launch}.py` | Settings, spec parsing, and game launching |
| CLI | `risk_bench/main.py` | Entry point: the `game`, `tournament`, `evaluate`, `view`, and `serve` commands |

See [execution and evaluation details](v3.md) for journal checkpoints and research sidecars,
and [forking and faithful resume](forking.md) for replay boundaries and context edits.
