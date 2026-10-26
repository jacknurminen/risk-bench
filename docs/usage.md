# Running games, tournaments, and evaluations

[Overview](../README.md) · [Usage](usage.md) · [Research](research.md) · [Architecture](architecture.md)

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
ANTHROPIC_AALTO_API_KEY=your-anthropic-research-api-key
OPENAI_API_KEY=your-openai-api-key
GOOGLE_API_KEY=your-google-api-key
```

Only selected providers need keys. Settings load from `.env` or environment variables;
`.env` is excluded from Git. Random players require no keys.

All Anthropic players, commentators, and evaluators require `ANTHROPIC_AALTO_API_KEY`.
The legacy `ANTHROPIC_API_KEY` is not used for requests. If the research key is
unset or empty, creating an Anthropic agent fails with a configuration error.
After adding the key to `~/.zshrc`, run `source ~/.zshrc` in the terminal that
launches the server. Restart an existing server to apply the updated environment.

Start the web app:

```bash
uv run python -m risk_bench.main serve --port 8420
```

Open [http://localhost:8420](http://localhost:8420). Games are saved under `data/v3/games/`.

## Command-line usage

The browser is enough to play. These commands are for running agent-only games, tournaments,
evaluations, and replay exports directly from the terminal, after completing the setup above.

Players are given inline as specs — `random` or `provider:model` (providers `anthropic`/`openai`/`google`), e.g. `anthropic:claude-opus-5-5`. Any model id the provider accepts works; the registry in `risk_bench/agents/models.py` is what the setup form offers and what usage reports can price, not a whitelist. There is no separate registration step.

Gemini 4 Argon is available under Google in both player and evaluator pickers, with
`low`, `medium`, and `high` reasoning effort (CLI example: `google:gemini-4-argon:medium`).
Its registered introductory rates are $2 input, $0.10 cached input, and $10 output per
million tokens, including thinking tokens. These rates apply through November 30, 2026;
Google lists $4/$0.20/$20 from December 1. The registry stores fixed rates and does not
switch automatically. For Argon, Google recommends an output budget of at least 32,768
tokens; set `MAX_AGENT_TOKENS=32768` in `.env` for gameplay. The evaluator default
of 64,000 already exceeds that recommendation. These settings apply to all models in that role.
See Google's [Argon guide](https://ai.google.dev/gemini-api/docs/latest-model)
and [pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-4-argon).

Whole-game evaluators have a 64,000-token output budget per call, including provider
reasoning tokens. Override it with `MAX_EVALUATOR_TOKENS` in `.env`. Opus 5.5 supports
up to 128,000 output tokens on the regular Messages API, so an Opus-only panel can use
`MAX_EVALUATOR_TOKENS=128000`. Choose a budget supported by every model in a mixed panel.
New evaluation workers load the setting when they start; changing it does not alter a
request already running. See [Opus 5.5 limits](https://platform.claude.com/docs/en/models/opus-5-5/overview).

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

Render a finished game into a standalone HTML replay:

```bash
uv run python -m risk_bench.main view data/v3/games/<game-folder>/game.jsonl
```

## Experiment player

The setup page has an optional **Experiment player** section, separate from the ordinary
players and research evaluator. Select **Mission Prober** or **Deceiver**, then choose its
model and reasoning effort. This adds one participant to the roster; a game still needs
3–6 players in total. For a three-player experiment, enable the profile and remove one
ordinary player, leaving two ordinary players plus the experiment player.

Mission Prober prioritizes inferring opponents' missions through targeted questions.
Deceiver seeks informative deception attempts across opponents, including inducing
military decisions, exploiting openings, and negotiating again after pact violations.
The selected objective replaces the normal winning objective
for that player; the game rules, secret mission, tool requirements, and visibility rules
remain intact. The shared experiment role and selected profile are appended to its system
prompt for actions, negotiation, and notes updates. Other players receive their normal prompts.
Use **View experiment instructions** to inspect the objective and complete profile before launch.

Prompt sources are `docs/prompts/experiment-player-v1.txt`,
`docs/prompts/mission-inference-prober-v1.txt`, and `docs/prompts/deceiver-v1.txt`.
Each launch saves the exact objective, instructions, and hash. The profile stays attached to
its roster player when seats shuffle or cycle, including duplicate model selections.
Resume, forks, tournament continuation, and tournament replay preserve the saved profile;
editing the files affects newly launched experiments.

## Tournament controls

### Seating

Web setup has a **Random / Cycle** seating toggle. Random uses a seed-derived shuffle for
each game. Cycle runs through all seat permutations in roster order, repeating as needed.
With three players and 12 games, each of the six orders runs twice and each player occupies
each seat four times. The resolved schedule is saved before launch and survives queue
cancellation and restart. Missions and starting territories remain generated by the game seed;
private context stays attached to its player. The API setting is `seating_mode: "random" | "cycle"`.

### Parallel games

Web setup separates **total games** from **parallel games**. For example, total games `10`
and parallel games `2` queues ten games and runs at most two workers at once. A slot includes
the game's automatic evaluation; when the worker finishes or pauses, the next queued game starts.
Queued games appear in the game list and can be cancelled before they start. Resuming a game
also respects its tournament's limit. Stopping the server cancels the in-memory queue and pauses
running games; saved launch settings let you start waiting games again from the list.

### Continue a tournament

On a tournament's stats page, **Continue tournament** adds more games to the same tournament.
It reuses the saved launch setup, continues with the next seeds and seating-cycle positions,
and keeps the existing parallel-game limit. New and existing games contribute to the same
aggregate stats. Previous evaluation versions retain their original game coverage; continuation
switches the report to **Latest per game** to include the additions.

### Replay a game

**Replay a tournament game** restarts one paused or finished game from the beginning, using
its seed, actual seat order and saved setup. The replacement occupies the same tournament slot:
the game count, seed sequence and seating cycle do not advance, and current aggregate stats count
only the replacement attempt. Original journals, traces and evaluations remain accessible under
**Previous game attempts**. Replays use the current code and prompts, so fixes apply to the new run.
Pause a running game (or cancel its queue entry) before replaying it; active evaluations must finish first.

## Evaluation modes and reruns

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
### Whole-game assessment and corrections

Whole-game evaluation starts from the original trace without importing live annotations.
Each evaluator returns a complete assessment and screening coverage in one call. Citation,
participant and chronology errors are returned for up to two full-response corrections before
saving. Whole-game analysis can also make one focused citation-repair call if a response
refers to missing catalog entries. Repairs must supply exact source evidence and pass the
usual validation. Remaining review flags stay visible. If a later correction fails, the last
structurally complete assessment is retained with its review flags and a correction note;
runs with no complete assessment fail.

Prior evaluations are archived for comparison. Replaying rounds instead retains an incremental
ledger within the new run.
