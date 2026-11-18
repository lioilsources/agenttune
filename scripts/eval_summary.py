"""Summarise evalctl results under a directory: one row per (model, task), then totals per model.

Usage: eval_summary.py <dir>
"""
import collections
import glob
import json
import os
import sys

rows = []
for f in sorted(glob.glob(os.path.join(sys.argv[1], "**", "*.jsonl"), recursive=True)):
    with open(f) as fh:
        rows += [json.loads(x) for x in fh if x.strip()]
print("| model | task | pass | agent | test | violations | turns | tokens | s |")
print("|---|---|---|---|---|---|---|---|---|")
for r in sorted(rows, key=lambda r: (r["task"], r["model"])):
    print(f"| {r['model']} | {r['task']} | {'yes' if r['pass'] else 'no'} | {r['agent_exit']} | {r['test_exit']} | "
          f"{r['rule_violations']} | {r['turns']} | {r['tokens']} | {r['wall_time_s']:.0f} |"
          + (f" {r['error'][:100]}" if r.get("error") else ""))
by = collections.defaultdict(list)
for r in rows:
    by[r["model"]].append(r)
if len(by) > 1 or len(rows) > 1:
    print("\n| model | tasks | pass | violations | median s |\n|---|---|---|---|---|")
    for m, rs in sorted(by.items()):
        ts = sorted(r["wall_time_s"] for r in rs)
        print(f"| {m} | {len(rs)} | {sum(r['pass'] for r in rs)} | {sum(r['rule_violations'] for r in rs)} | "
              f"{ts[len(ts) // 2]:.0f} |")
