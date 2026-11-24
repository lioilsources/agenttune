#!/usr/bin/env python3
"""go-errors-wrap DPO pairs from the Go history of local repos.

    python3 data/synth_go.py --dev-root <dir with repos> --tasks evals/tasks --out data/out

Transcripts hold almost no Go edits, so the rule mutation in build.py finds
nothing to mutate. Here a real commit hunk that returns
fmt.Errorf("…: %w", err) is `chosen` (an edit call from the old to the new
hunk) and the same edit with that one return unwrapped to a bare `return err`
is `rejected`. Only hunks the eval would then flag are kept.

Every eval task repo (dev and holdout) is skipped whole, and so are excluded
projects. Strings go through the same scrubber as build.py; a hunk with a
secret is dropped. Writes dpo_go.jsonl and dpo_go_eval.jsonl, prints counts.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agentdata import build, dpo, gitlabel, rules, tools  # noqa: E402

MAX_LINES = 120      # an edit hunk, old or new side
MAX_NEW_FILE = 200   # a new file becomes one write call


def task_repos(tasks_dir):
    out = set()
    for f in Path(tasks_dir).glob("*/task.yaml"):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^repo:\s*(\S+)", line)
            if m:
                out.add(m.group(1).strip("\"'").split("/")[-1].lower())
    return out


def go_repos(root):
    for d in sorted(Path(root).iterdir()):
        if (d / ".git").is_dir() and (any(d.glob("go.mod")) or any(d.glob("*/go.mod"))):
            yield d


def hunks(patch):
    """(path, old text, new text) for every hunk of a non-test .go file in a `git log -p` body."""
    path = None
    old, new, inside = [], [], False

    def flush():
        if inside and path:
            return path, "\n".join(old) + "\n", "\n".join(new) + "\n"
        return None

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            if (h := flush()):
                yield h
            path, inside, old, new = None, False, [], []
            m = re.match(r"diff --git a/(\S+) b/(\S+)", line)
            p = m.group(2) if m else ""
            path = p if rules.is_go(p) and not rules.is_go_test(p) else None
        elif line.startswith("@@"):
            if (h := flush()):
                yield h
            inside, old, new = True, [], []
        elif inside and line[:1] in (" ", "-", "+") and not line.startswith(("--- ", "+++ ")):
            body = line[1:]
            if line[0] != "+":
                old.append(body)
            if line[0] != "-":
                new.append(body)
    if (h := flush()):
        yield h


def commits(repo):
    out = subprocess.run(["git", "-C", str(repo), "log", "--no-merges", "-U10", "-p", "--format=%x00%H%x01%s",
                          "-G", r"fmt\.Errorf\(.*%w", "--", "*.go"],
                         capture_output=True, text=True, errors="replace").stdout
    for block in out.split("\x00")[1:]:
        head, _, patch = block.partition("\n")
        sha, _, subject = head.partition("\x01")
        yield sha, subject, patch


def pair(system, subject, path, old, new):
    """(prompt, chosen, rejected); an empty `old` is a new file, written in one call."""
    user = {"role": "user", "content": f"{subject}\n\nSoubor: {path}"}
    if old.strip():
        name, args = "edit", {"filePath": path, "oldString": old, "newString": new}
        read = {"id": "call_1", "type": "function",
                "function": {"name": "read", "arguments": json.dumps({"filePath": path}, ensure_ascii=False)}}
        prompt = [system, user, {"role": "assistant", "content": "", "tool_calls": [read]},
                  {"role": "tool", "tool_call_id": "call_1", "content": old}]
    else:
        name, args = "write", {"filePath": path, "content": new}
        prompt = [system, user]
    chosen = {"role": "assistant", "content": "",
              "tool_calls": [{"id": "call_2", "type": "function",
                              "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
    rej = dpo.mutate(chosen, "go-errors-wrap")
    return None if rej is None else (prompt, chosen, rej)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev-root", required=True)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--private", default=str(HERE / "config.private.json"))
    ap.add_argument("--system-prompt", default=str(HERE / "system_prompt.md"))
    ap.add_argument("--eval-pct", type=int, default=5)
    ap.add_argument("--max-per-repo", type=int, default=40)
    ap.add_argument("--max-total", type=int, default=400)
    a = ap.parse_args()

    private = json.loads(Path(a.private).read_text())
    s, _, _ = build.make_scrubber(private)
    system = {"role": "system", "content": Path(a.system_prompt).read_text().strip()}
    skip = task_repos(a.tasks)
    fixes, _ = gitlabel.task_info(a.tasks)
    words = [w for w in private.get("exclude", []) if w]
    excluded = re.compile("|".join(map(re.escape, words)), re.I) if words else None
    held = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, sorted(skip))) + r")(?![A-Za-z0-9])",
                      re.I) if skip else None

    stats, seen = Counter(), set()
    rows = {"train": [], "eval": []}
    for repo in go_repos(a.dev_root):
        name = repo.name.lower()
        if name in skip or name.removesuffix("-l10n") in skip:
            stats["repo_eval_task"] += 1
            continue
        if excluded and excluded.search(repo.name):
            stats["repo_excluded"] += 1
            continue
        stats["repos"] += 1
        n_repo = 0
        for sha, subject, patch in commits(repo):
            if any(sha.lower().startswith(f) or f.startswith(sha.lower()) for f in fixes):
                stats["skip_eval_fix"] += 1
                continue
            for path, old, new in hunks(patch):
                if n_repo >= a.max_per_repo or sum(map(len, rows.values())) >= a.max_total:
                    stats["capped"] += 1
                    continue
                limit = MAX_LINES if old.strip() else MAX_NEW_FILE
                if max(old.count("\n"), new.count("\n")) > limit:
                    stats["skip_size"] += 1
                    continue
                key = hashlib.sha1((path + old + new).encode()).hexdigest()
                if key in seen:
                    stats["skip_duplicate"] += 1
                    continue
                text = subject + path + old + new
                if (held and held.search(text)) or (excluded and excluded.search(text)):
                    stats["skip_names_eval_or_excluded"] += 1
                    continue
                if s.find_secret(text):
                    stats["skip_secret"] += 1
                    continue
                p = pair(system, s.clean(subject), s.clean(path), s.clean(old), s.clean(new))
                if p is None:
                    stats["skip_no_mutation"] += 1
                    continue
                seen.add(key)
                prompt, chosen, rej = p
                split = build._split(f"{name}:{sha}", a.eval_pct)
                rows[split].append({"prompt": prompt, "chosen": [chosen], "rejected": [rej], "tools": tools.TOOLS,
                                    "meta": {"source": "git-history", "project": name,
                                             "session": hashlib.sha1(f"{name}:{sha}".encode()).hexdigest()[:12],
                                             "category": "rule-mutation", "rule": "go-errors-wrap"}})
                n_repo += 1
        stats[f"pairs_{name}"] = n_repo

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for split, fname in (("train", "dpo_go.jsonl"), ("eval", "dpo_go_eval.jsonl")):
        with open(out / fname, "w") as fh:
            for r in rows[split]:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        stats[f"out_{fname}"] = len(rows[split])
    json.dump(dict(sorted(stats.items())), sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
