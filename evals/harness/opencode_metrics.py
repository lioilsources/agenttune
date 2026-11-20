"""Sum token usage and count model steps from `opencode run --format json` events.

Prints {"turns": N, "tokens": N} for evalctl's metrics file. The event schema is
not stable across OpenCode versions, so it looks for any "tokens" object and any
event whose type marks a finished step.
"""
import json
import sys


def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def main(path):
    turns = tokens = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if str(ev.get("type", "")).replace("-", "_") == "step_finish":
                turns += 1
            for d in walk(ev):
                t = d.get("tokens")
                if isinstance(t, dict):
                    tokens += sum(v for k, v in t.items() if k in ("input", "output", "reasoning") and isinstance(v, int))
                    break
    print(json.dumps({"turns": turns, "tokens": tokens}))


if __name__ == "__main__":
    main(sys.argv[1])
