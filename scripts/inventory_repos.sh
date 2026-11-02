#!/usr/bin/env bash
# Repos under $1 with tests, and commits that change code and tests together (eval-task candidates).
set -u
root=${1:?usage: inventory_repos.sh <dir with repos>}
printf '| repo | lang | test files | commits | commits code+test (all time) | last commit |\n|---|---|---:|---:|---:|---|\n'
for d in "$root"/*/; do
  [ -d "$d/.git" ] || continue
  r=$(basename "$d"); lang=""; n=0
  if [ -f "$d/go.mod" ] || ls "$d"/*/go.mod >/dev/null 2>&1; then lang=go; n=$(git -C "$d" ls-files '*_test.go' | wc -l | tr -d ' '); fi
  if [ -f "$d/pubspec.yaml" ] || ls "$d"/*/pubspec.yaml >/dev/null 2>&1; then lang="${lang:+$lang+}flutter"; n=$((n + $(git -C "$d" ls-files '*_test.dart' | wc -l | tr -d ' '))); fi
  [ -n "$lang" ] || continue
  total=$(git -C "$d" rev-list --count HEAD 2>/dev/null || echo 0)
  both=$(git -C "$d" log --no-merges --format='@%h' --name-only 2>/dev/null | awk '
    /^@/ { if (c && t) k++; c=0; t=0; next }
    /_test\.(go|dart)$/ { t=1; next }
    /\.(go|dart)$/ { c=1 }
    END { if (c && t) k++; print k+0 }')
  last=$(git -C "$d" log -1 --format=%cs 2>/dev/null)
  printf '| %s | %s | %s | %s | %s | %s |\n' "$r" "$lang" "$n" "$total" "$both" "$last"
done
