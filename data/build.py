#!/usr/bin/env python3
"""Build the UC27 datasets from local Claude Code transcripts.

    python3 data/build.py --raw data/raw/projects --tasks evals/tasks --out data/out

Runs locally only. Reads data/config.private.json (gitignored) for the
owner's names and the .env globs whose secret values must never appear in
the output. Prints counts only, never content.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agentdata import build  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=str(HERE / "raw" / "projects"))
    ap.add_argument("--tasks", help="evals/tasks dir (task.yaml with fix: and split:); without it no eval filter")
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--private", default=str(HERE / "config.private.json"))
    ap.add_argument("--system-prompt", default=str(HERE / "system_prompt.md"))
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--eval-pct", type=int, default=5)
    ap.add_argument("--cap-per-rule", type=int, default=400)
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--max-calls", type=int, default=30, help="SFT: drop trajectories with more tool calls (0 = off)")
    ap.add_argument("--max-repeat", type=int, default=2, help="SFT: drop when one identical call repeats more often (0 = off)")
    a = ap.parse_args()

    priv = Path(a.private)
    if not priv.exists():
        sys.exit(f"missing {priv}: copy config.private.example.json and fill it in")
    cfg = build.Config(raw=a.raw, out=a.out, system_prompt=Path(a.system_prompt).read_text(),
                       private=json.loads(priv.read_text()), max_tokens=a.max_tokens,
                       eval_pct=a.eval_pct, cap_per_rule=a.cap_per_rule, sample=a.sample, tasks=a.tasks,
                       max_calls=a.max_calls, max_repeat=a.max_repeat)
    report = build.run(cfg)
    json.dump(report, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
