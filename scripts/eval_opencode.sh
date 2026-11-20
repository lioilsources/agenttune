#!/usr/bin/env bash
# Run the eval with OpenCode as the agent. Loads .env (AGENTTUNE_LLM_BASE_URL).
# Usage: eval_opencode.sh <model> <repos-root> [evalctl run flags...]
# SPARK: wrap in `sparkctl exec <project> qwen36 <min> ... --`. L40S: needs its window and an ssh tunnel.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }
model=$1 root=$2; shift 2
go build -o bin/evalctl ./cmd/evalctl
exec bin/evalctl run -model "$model" -repos-root "$root" \
  -agent-cmd "$PWD/evals/harness/opencode.sh {workdir} {prompt_file} {model} {metrics_file}" "$@"
