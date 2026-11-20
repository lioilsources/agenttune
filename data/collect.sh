#!/usr/bin/env bash
# Copy Claude Code transcripts into data/raw/projects (incremental, never deletes),
# so Claude Code's cleanup does not lose them. Stays on this machine: data/raw is
# not in git and is never copied to a training host.
set -euo pipefail
cd "$(dirname "$0")/.."
src=${CLAUDE_PROJECTS:-$HOME/.claude/projects}
mkdir -p data/raw/projects
rsync -a --prune-empty-dirs \
  --include='*/' --include='*.jsonl' --exclude='*' \
  "$src/" data/raw/projects/
find data/raw/projects -name '*.jsonl' | wc -l | xargs echo "transcripts in data/raw/projects:"
