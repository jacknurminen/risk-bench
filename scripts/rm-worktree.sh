#!/usr/bin/env bash
#
# Tear down a worktree created by new-worktree.sh, given the same branch name.
# Removes the adjacent "<repo>-<branch>" directory and prunes stale worktree
# metadata. Optionally deletes the branch too.
#
# Usage:
#   scripts/rm-worktree.sh <branch-name> [--force] [--delete-branch]
#
# Examples:
#   scripts/rm-worktree.sh commentary-panel                  # remove the worktree
#   scripts/rm-worktree.sh commentary-panel --delete-branch  # ...and delete the branch
#   scripts/rm-worktree.sh commentary-panel --force          # discard uncommitted changes

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $(basename "$0") <branch-name> [--force] [--delete-branch]" >&2
    exit 1
fi

branch="$1"; shift
force=false
delete_branch=false
for arg in "$@"; do
    case "$arg" in
        --force)         force=true ;;
        --delete-branch) delete_branch=true ;;
        *) echo "error: unknown option '$arg'" >&2; exit 1 ;;
    esac
done

repo_root="$(git rev-parse --show-toplevel)"
repo_name="$(basename "$repo_root")"
safe_name="${branch//\//-}"
worktree_path="$(dirname "$repo_root")/${repo_name}-${safe_name}"

if [[ ! -e "$worktree_path" ]]; then
    echo "error: no worktree at $worktree_path" >&2
    exit 1
fi

# git refuses to remove a worktree with uncommitted changes unless forced.
if [[ "$force" == true ]]; then
    echo ">> removing $worktree_path (forced)"
    git worktree remove --force "$worktree_path"
else
    echo ">> removing $worktree_path"
    git worktree remove "$worktree_path"
fi

git worktree prune

if [[ "$delete_branch" == true ]]; then
    echo ">> deleting branch '$branch'"
    git branch -D "$branch"
fi

echo "Done."