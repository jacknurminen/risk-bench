# Agent Guidelines

These rules MUST be followed by all AI coding agents.

## Communication

- When uncertain, ask clarifying questions rather than making assumptions
- If the user is just asking an informational question, provide an answer and don't begin implementation until the go-ahead from the user
- If it is evident they are asking you to implement something you can go ahead and start implementation
- Explain the reasoning behind non-obvious decisions

## Log

- **Objective:** Maintain a detailed log of the project's development
- **Location:** The private sibling `risk-bench-thesis` repository, under `notes/development/`, with one file per day. Do not create or commit development logs in this repository.
- **Content:** Record significant changes to the project's *core logic*, *algorithms*, or *architecture*. Explicitly explain the *reasoning* behind these changes.
- **Exclusions:** Do NOT include administrative tasks (like updating these guidelines), configuration tweaks, or minor refactoring unrelated to the core logic.

## Testing

Run the tests relevant to the change with `uv run pytest` and, for browser logic, `node --test tests/ui/*.test.cjs`.

## Worktrees

When asked to create or work in a git worktree, use the helper scripts rather than raw `git worktree` — they handle the per-worktree setup (gitignored `.venv` and `data/`) that a bare checkout misses.

- **Create:** `scripts/new-worktree.sh <branch> [base-ref] [--seed-data]` — creates `../risk-bench-<branch>` adjacent to the repo, copies `.env` if present, runs `uv sync`.
- **Tear down:** `scripts/rm-worktree.sh <branch> [--force] [--delete-branch]`.
