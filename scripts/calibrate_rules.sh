#!/usr/bin/env bash
# Run every rule over the code+test commits of local repos and count violations per rule.
# Usage: calibrate_rules.sh <evalctl> <repos-root> <out-dir> <repo>...
set -euo pipefail
E=$1 root=$2 out=$3; shift 3
mkdir -p "$out"
for r in "$@"; do
  f="$out/calib-$r.jsonl"; : > "$f"; n=0
  while read -r parent sha; do
    git -C "$root/$r" diff --no-color "$parent" "$sha" | "$E" rules check -C "$root/$r" - >> "$f" || true
    n=$((n + 1))
  done < <("$E" candidates -n "${N:-40}" "$root/$r" | python3 -I -c 'import json,sys
for l in sys.stdin: d=json.loads(l); print(d["parent"], d["sha"])')
  printf '%s commits=%d %s\n' "$r" "$n" "$(python3 -I -c 'import json,sys,collections
print(dict(collections.Counter(json.loads(l)["rule"] for l in open(sys.argv[1]))))' "$f")"
done
