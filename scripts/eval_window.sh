#!/usr/bin/env bash
# Baseline eval in a GPU window owned by another host's queue.
# Waits for a fresh READY marker, runs smoke → dev → holdout with OpenCode,
# stops at the window guard and writes the DONE marker.
# Host details come from .env (not committed):
#   WINDOW_SSH        ssh alias of the GPU host
#   WINDOW_READY      READY marker path on the host
#   WINDOW_DONE       DONE marker path on the host
#   WINDOW_PORT       model port on the host, tunnelled to the same local port
#   WINDOW_MIN        minutes from READY until the guard stops the run (default 170)
#   EVAL_MODEL        model name for the agent (e.g. qwen3-coder)
# Usage: eval_window.sh <repos-root>
set -uo pipefail
cd "$(dirname "$0")/.."
A=$PWD
[ -f .env ] && { set -a; . ./.env; set +a; }
: "${WINDOW_SSH:?}" "${WINDOW_READY:?}" "${WINDOW_DONE:?}" "${WINDOW_PORT:?}" "${EVAL_MODEL:?}"
R=${1:?repos-root}
log() { echo "$(date '+%H:%M:%S') $*"; }
up() { curl -sf "127.0.0.1:$WINDOW_PORT/v1/models" >/dev/null; }

since=$(date +%s); log "waiting for READY newer than now"
until ssh -n -o ConnectTimeout=10 "$WINDOW_SSH" "f=$WINDOW_READY; [ -f \$f ] && [ \$(stat -c %Y \$f) -ge $((since - 60)) ]" 2>/dev/null; do sleep 60; done
deadline=$(( $(date +%s) + ${WINDOW_MIN:-170} * 60 )); log "READY"

tun=
if ! up; then
  ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L "$WINDOW_PORT:localhost:$WINDOW_PORT" "$WINDOW_SSH" & tun=$!
fi
for _ in $(seq 1 30); do up && break; sleep 10; done
up || { log "endpoint not up"; exit 1; }
done_marker() { ssh -n "$WINDOW_SSH" touch "$WINDOW_DONE" && log "DONE signalled"; [ -n "$tun" ] && kill "$tun"; }

run() { # run <label> <evalctl flags...>; killed at the window guard
  log "start $1"; shift
  scripts/eval_opencode.sh "$EVAL_MODEL" "$R" "$@" & p=$!
  while kill -0 $p 2>/dev/null; do
    [ "$(date +%s)" -ge "$deadline" ] && { log "guard reached, stopping"; pkill -P $p; kill $p; }
    sleep 20
  done
  wait $p; log "exit $?"
}

run smoke -only promoclown-min-days-zero-001
if grep -q "agent exit" "$(ls -t "$A"/evals/results/*.md | head -1)"; then
  log "smoke: agent failed, stopping"; done_marker; exit 1
fi
run dev -split dev
[ $((deadline - $(date +%s))) -gt 1800 ] && run holdout -split holdout
done_marker
