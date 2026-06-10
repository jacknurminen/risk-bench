# Agent Guidelines

These rules MUST be followed by all AI coding agents.

## Communication

- When uncertain, ask clarifying questions rather than making assumptions
- If the user is just asking an informational question, provide an answer and don't begin implementation until the go-ahead from the user
- If it is evident they are asking you to implement something you can go ahead and start implementation
- Explain the reasoning behind non-obvious decisions

## Log

- **Objective:** Maintain a detailed log of the project's development
- **Location:** `docs/log/` with one file per day (e.g., `2026-01-11.md`).
- **Content:** Record significant changes to the project's *core logic*, *algorithms*, or *architecture*. Explicitly explain the *reasoning* behind these changes.
- **Exclusions:** Do NOT include administrative tasks (like updating these guidelines), configuration tweaks, or minor refactoring unrelated to the core logic.

## Testing

See [Testing Guidelines](../docs/testing.md) for testing philosophy and practices.
