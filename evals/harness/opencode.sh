#!/usr/bin/env bash
# Eval agent: OpenCode against an OpenAI-compatible endpoint (SPARK gateway, L40S vLLM).
# Usage (agent-cmd template): opencode.sh {workdir} {prompt_file} {model} {metrics_file}
set -euo pipefail
: "${AGENTTUNE_LLM_BASE_URL:?set AGENTTUNE_LLM_BASE_URL (gateway /v1 URL, see .env.example)}"
export AGENTTUNE_LLM_API_KEY="${AGENTTUNE_LLM_API_KEY:-dummy}"
here=$(cd "$(dirname "$0")" && pwd)
export OPENCODE_CONFIG="$here/opencode.json"
# Event log outside the clone, written as it goes: survives the run and a timeout kill.
keep=${EVENTS_KEEP_DIR:-$here/../results/events}; mkdir -p "$keep"
events="$keep/$(date +%Y%m%d-%H%M%S)-$3.events.jsonl"
# Sandbox. The agent runs on the owner's Mac with bash allowed, and a model tuned
# on the owner's sessions knows the owner's tools: it once opened a SPARK window
# and closed a broker job from inside an eval. So:
# - no Claude Code prompt, skills or plugins from the owner's home;
# - its own HOME (build caches pinned to the real ones);
# - tools that reach other machines or the GPU broker are stubs that refuse;
# - the clone cannot push back into the source repo.
real_home=$HOME
export GOPATH=$(go env GOPATH) GOMODCACHE=$(go env GOMODCACHE) GOCACHE=$(go env GOCACHE)
export PUB_CACHE=${PUB_CACHE:-$(readlink -f "$real_home/.pub-cache" 2>/dev/null || echo "$real_home/.pub-cache")}
export OPENCODE_DISABLE_CLAUDE_CODE=1 OPENCODE_DISABLE_CLAUDE_CODE_PROMPT=1 OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1 \
  OPENCODE_DISABLE_EXTERNAL_SKILLS=1 OPENCODE_DISABLE_DEFAULT_PLUGINS=1 OPENCODE_DISABLE_AUTOUPDATE=1 \
  OPENCODE_DISABLE_MODELS_FETCH=1 OPENCODE_DISABLE_SHARE=1
sb=$(dirname "$4")/sandbox; mkdir -p "$sb/home" "$sb/bin"
for t in sparkctl qctl ssh scp sftp rsync gh docker tailscale jodadeploy; do
  printf '#!/bin/sh\necho "%s: not available in the eval sandbox" >&2\nexit 126\n' "$t" > "$sb/bin/$t"; chmod +x "$sb/bin/$t"
done
export HOME="$sb/home"
export PATH="$sb/bin:$(echo "$PATH" | tr ':' '\n' | grep -v -e "^$real_home/.local/bin" -e "^$real_home/bin" | paste -sd: -)"
git -C "$1" remote set-url --push origin /dev/null/push-blocked 2>/dev/null || true
# Own data/state dir per run: no clash with the user's OpenCode sessions or DB schema.
export XDG_DATA_HOME="$(dirname "$4")/opencode-data" XDG_STATE_HOME="$(dirname "$4")/opencode-state"
rc=0
opencode run --pure --dir "$1" -m "llm/$3" --format json "$(cat "$2")" > "$events" || rc=$?
python3 -I "$here/opencode_metrics.py" "$events" > "$4" || true
exit "$rc"
