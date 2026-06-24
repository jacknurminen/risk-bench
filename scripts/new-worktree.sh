#!/usr/bin/env bash
#
# Create a git worktree for a new (or existing) branch and set it up ready to
# work in: venv synced, .env carried over, data/ optionally seeded.
#
# The worktree is created adjacent to this repo, named "<repo>-<branch>", e.g.
# /home/jack/repos/risk-bench  ->  /home/jack/repos/risk-bench-commentary-panel
#
# Usage:
#   scripts/new-worktree.sh <branch-name> [base-ref] [--seed-data]
#
# Examples:
#   scripts/new-worktree.sh commentary-panel            # branch off current HEAD
#   scripts/new-worktree.sh hotfix-x main --seed-data   # branch off main, copy data/

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $(basename "$0") <branch-name> [base-ref] [--seed-data]" >&2
    exit 1
fi

branch="$1"; shift
base_ref="HEAD"
seed_data=false
for arg in "$@"; do
    case "$arg" in
        --seed-data) seed_data=true ;;
        *)           base_ref="$arg" ;;
    esac
done

repo_root="$(git rev-parse --show-toplevel)"
repo_name="$(basename "$repo_root")"
# Slashes in branch names (feature/foo) aren't valid in a dir name.
safe_name="${branch//\//-}"
worktree_path="$(dirname "$repo_root")/${repo_name}-${safe_name}"

if [[ -e "$worktree_path" ]]; then
    echo "error: $worktree_path already exists" >&2
    exit 1
fi

# Reuse the branch if it already exists, otherwise create it off base_ref.
if git show-ref --quiet --verify "refs/heads/$branch"; then
    echo ">> worktree for existing branch '$branch' at $worktree_path"
    git worktree add "$worktree_path" "$branch"
else
    echo ">> new branch '$branch' off '$base_ref' at $worktree_path"
    git worktree add -b "$branch" "$worktree_path" "$base_ref"
fi

# Carry over untracked-but-needed files.
if [[ -f "$repo_root/.env" ]]; then
    cp "$repo_root/.env" "$worktree_path/.env"
    echo ">> copied .env"
fi

if [[ "$seed_data" == true && -d "$repo_root/data" ]]; then
    cp -R "$repo_root/data" "$worktree_path/data"
    echo ">> seeded data/ from main checkout"
fi

# Build the per-worktree virtual environment.
echo ">> uv sync"
( cd "$worktree_path" && uv sync )

echo
echo "Done. Next:"
echo "  cd $worktree_path"
echo "  claude          # start a session isolated to this branch"