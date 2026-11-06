#!/usr/bin/env bash
# Eval agent: OpenCode against an OpenAI-compatible endpoint (SPARK gateway, L40S vLLM).
# Usage (agent-cmd template): opencode.sh {workdir} {prompt_file} {model} {metrics_file}
set -euo pipefail
: "${AGENTTUNE_LLM_BASE_URL:?set AGENTTUNE_LLM_BASE_URL (gateway /v1 URL, see .env.example)}"
export AGENTTUNE_LLM_API_KEY="${AGENTTUNE_LLM_API_KEY:-dummy}"
here=$(cd "$(dirname "$0")" && pwd)
export OPENCODE_CONFIG="$here/opencode.json"
events="$4.events.jsonl"
rc=0
opencode run --pure --dir "$1" -m "llm/$3" --format json "$(cat "$2")" > "$events" || rc=$?
python3 -I "$here/opencode_metrics.py" "$events" > "$4" || true
exit "$rc"
