# Python Code Quality Guidelines

This document provides guidelines for maintaining high-quality Python code.

## Code Style and Formatting

- **MUST** use meaningful, descriptive variable and function names that exactly describe what they are or do (for example `get_active_users` is better than `get_users` if that's specifically what it does)
- **NEVER** use abbreviations in names (e.g., use `validation` not `val`, `message` not `msg`)
- Optimize for readability; utilize line length to add detail through naming (within reason—too long becomes hard to read)
- Consider context: Don't add obvious information in naming like `get_user_from_database` if it's in a repository layer, `get_user` is fine and understandable in the context
- Limit line length to 119 characters
- Aim for "glanceability": code should be visually blocky and flow naturally so you can quickly scan and understand the structure. Avoid "choppy" code with many short scattered statements that break up the visual flow and make it hard to follow.
- **MUST** use comments as visual separators for grouping related items in long lists (e.g., function arguments, class attributes) when it helps distinguish natural groupings and improves readability.

## Documentation

- **MUST** include docstrings for all public functions and methods
- **NEVER** use docstrings for private functions/methods
- **MUST** document public function parameters, return values, and exceptions raised
- Prefer self-documenting code through descriptive variable names and clear composition over explicit comments
- Inline comments should explain WHY, not WHAT; only add them in non-obvious cases where extra context is needed to understand the logic

## Module and Class Structure

- Public functions and methods should be defined at the top of the module or class
- Private functions and methods (prefixed with `_`) should follow, ordered by their calling sequence from the public ones
- This creates a top-down readable flow: readers see the API first, then implementation details
- For complex modules or classes, compose public functions/methods to read like English descriptions of the logic steps, delegating actual implementation to private ones (for simple cases, keeping logic in public ones is fine)

## API Design and Visibility

- **MUST** be thoughtful about the publicly exposed API of modules and classes
- **MUST** default to private visibility (prefixed with `_`) for all functions, methods, and classes; only expose what is strictly necessary
- **MUST** be very intentional about what is exposed publicly
- This informs the reader about how the module/class should be used and differentiates between the public interface and internal implementation details

Example of a well-documented and composed public function:

```python
def process_and_submit_order(order: Order, user: User) -> OrderConfirmation:
    """Process an order through validation, discounts, and fulfillment.

    Args:
        order: The order to process
        user: The user placing the order, used for discount eligibility

    Returns:
        Confirmation details from the fulfillment service

    Raises:
        ValidationError: If order contains invalid or out-of-stock items
        FulfillmentError: If the fulfillment service is unavailable
    """
    validated_order = _validate_order_items(order=order)
    discounted_order = _apply_user_discounts(order=validated_order, user=user)
    final_order = _calculate_shipping_and_totals(order=discounted_order)
    return _submit_order_to_fulfillment_service(order=final_order)
```

## Type Hints

- **MUST** use type hints for all function signatures (parameters and return values)
- **MUST** use Python 3.13+ typing conventions (e.g., `list[str]` not `List[str]`, `str | None` not `Optional[str]`)
- **MUST** fully specify generic types (e.g., `dict[str, int]` not `dict`, `list[User]` not `list`)
- **NEVER** use `Any` type unless absolutely necessary
- **NEVER** use `cast`

## Error Handling

- **MUST** be sparing with error handling, don't use it too excessively, only if it's relevant to handle in some specific ways.

## Function Design

- **MUST** always use named arguments when calling functions (e.g., `process_user(user_id=user_id, validate=True)` not `process_user(user_id, True)`)
- **NEVER** use default argument values; always require explicit arguments (e.g., `def process(timeout: int)` not `def process(timeout: int = 30)`)
- **NEVER** use mutable objects (lists, dicts) as default argument values
- **MUST** define function signatures with only the strictly necessary arguments.
- **AVOID** passing large data containers (like whole User objects) if only specific fields (like `user_id`) are needed. This decoupling simplifies testing and refactoring.
- Return early to reduce nesting

## Class Design

- **MUST** keep `__init__` simple; avoid complex logic
- Use `@dataclass` for internal data containers (statically checked, no runtime overhead)
- Use Pydantic models for data at system boundaries (APIs, database repos, file I/O) that need serialization/deserialization or runtime validation
- This ensures validation happens at the borders while internal data relies on static type checking
- **NEVER** add methods to data container classes
- Prefer composition over inheritance

## Imports and Dependencies

- **MUST** place all imports at the top of the module; **NEVER** use imports within functions or methods.
- **MUST** avoid wildcard imports (`from module import *`)
- **MUST** document dependencies in `pyproject.toml`
- Use `uv` for fast package management and dependency resolution
- Add dependencies with `uv add <dependency>`
- Organize imports: standard library, third-party, local imports
- Import whole modules rather than individual functions when calling external services/modules (e.g., `import user_service` then `user_service.get_user(...)` rather than `from user_service import get_user`). This makes external calls clearly distinguishable from internal functions.

### Dependency Selection

- **PREFER** well-maintained and widely used dependencies. Always evaluate the quality, maintenance status (e.g., last commit, issue responsiveness), and community trust before adding.
- **MINIMIZE** use of large dependencies; only add them when strictly necessary for core functionality.
- **CONSIDER** implementing simple functionality from scratch instead of adding a dependency, especially if available options are poorly maintained or overly complex.
- **AVOID** dependency bloat: every new dependency is a potential security risk and maintenance burden.

## Python Best Practices

- **MUST** use f-strings for string formatting
- **PREFER** iterators, generator expressions, and `yield` over creating large intermediate lists or collections to minimize memory usage.
- Use list comprehensions and generator expressions
- Use `enumerate()` instead of manual counter variables

## Tools

- **MUST** use `ruff` for code formatting and linting. Run with `uv run ruff format` and `uv run ruff check --fix`
- **MUST** use `ty` for static type checking. Run with `uv run ty check`
- Use `uv` for package management
- Use `pytest` for testing