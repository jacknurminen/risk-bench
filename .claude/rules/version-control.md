# Version Control Guidelines

## Commit Messages

- Write for the future reader: imagine someone using git blame who has no context about the project and wants to understand why a change was made
- Commits are a form of documentation and communication
- The WHY is more important than the WHAT (though the WHAT can be included too)
- Commit title should be succinct but describe the main change
- Commit body should be more informative, explaining the motivation and context behind the change

## Diff Review and Grouping

- **MUST** review the full diff (`git diff HEAD`) before staging
- **MUST** check for new (untracked) files that may have been created (`git status`) to ensure they are considered in the commit grouping
- Analyze the changes: can they be grouped into distinct, logical units? (e.g., separating a bug fix from a style cleanup, or two distinct features)
- **Prefer** splitting changes into separate commits to improve history legibility over large monolithic commits
- **Constraint:** Ensure each split commit remains **atomic**; do not split changes if it results in a broken intermediate state (e.g. tests failing or code not compiling)

## Atomic Commits

- Each commit should be atomic: it contains all relevant changes needed to make the change work as a complete unit
- A commit should not leave the codebase in a broken state
- Related changes belong together; unrelated changes belong in separate commits

## Commit Sequencing

- For larger changes, commit from the "bottom" of the dependency graph: start with code that doesn't depend on anything, then commit code that depends on it in subsequent commits
- Consider the reviewer: when committed correctly, large changes can go in one PR where reviewing is easy commit by commit
- The commit list should be a logical telling of the implemented changes, like a story that builds up to the final result

## What Not to Commit

- **NEVER** commit commented-out code; delete it
- **NEVER** commit debug print statements or breakpoints
- **NEVER** commit credentials or sensitive data