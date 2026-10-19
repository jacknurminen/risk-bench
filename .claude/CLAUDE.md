# Agent Guidelines

These rules MUST be followed by all AI coding agents.

## Communication

- When uncertain, ask clarifying questions rather than making assumptions
- If the user is just asking an informational question, provide an answer and don't begin implementation until the go-ahead from the user
- If it is evident they are asking you to implement something you can go ahead and start implementation
- Explain the reasoning behind non-obvious decisions

## Testing

Run the tests relevant to the change with `uv run pytest` and, for browser logic, `node --test tests/ui/*.test.cjs`.
