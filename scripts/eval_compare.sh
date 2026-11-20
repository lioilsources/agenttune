#!/usr/bin/env bash
# Compare models task by task in one GPU window: for each task every model
# runs once before the next task starts, so a run cut short by the window
# guard still compares all models on the tasks it reached.
# Go tasks first (seconds per test run), Flutter last (minutes).
#
# Usage: eval_compare.sh <repos-root> <split> <out-dir> <model>...
# Needs AGENTTUNE_LLM_BASE_URL (tunnel to the endpoint). Optional DEADLINE
# (epoch seconds): no new task starts after it.
set -uo pipefail
cd "$(dirname "$0")/.."
R=${1:?repos-root} split=${2:?split} out=${3:?out-dir}; shift 3
models=("$@")
: "${AGENTTUNE_LLM_BASE_URL:?}"
log() { echo "$(date '+%H:%M:%S') $*"; }
go build -o bin/evalctl ./cmd/evalctl || exit 2

tasks=$(for f in evals/tasks/*/task.yaml; do
  grep -q "^split: holdout" "$f" && s=holdout || s=dev
  [ "$split" = all ] || [ "$split" = "$s" ] || continue
  l=$(awk '/^lang:/{print $2}' "$f")
  echo "$([ "$l" = flutter ] && echo 1 || echo 0) $(basename "$(dirname "$f")")"
done | sort | awk '{print $2}')

for t in $tasks; do
  for m in "${models[@]}"; do
    if [ -n "${DEADLINE:-}" ] && [ "$(date +%s)" -ge "$DEADLINE" ]; then log "deadline, stopping"; exit 0; fi
    d="$out/$m/$t"
    [ -s "$d"/*.jsonl ] 2>/dev/null && { log "skip $m $t (done)"; continue; }
    log "run $m $t"
    mkdir -p "$out/$m"
    bin/evalctl run -model "$m" -repos-root "$R" -split all -only "$t" -out "$d" \
      -agent-cmd "$PWD/evals/harness/opencode.sh {workdir} {prompt_file} {model} {metrics_file}" >/dev/null 2>"$d.log" \
      || log "evalctl exit $? ($m $t)"
    python3 -I scripts/eval_summary.py "$d" | tail -n +3
  done
done
log "all tasks done"
python3 -I scripts/eval_summary.py "$out"
