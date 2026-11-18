"""Turns transcripts into sft.jsonl, dpo.jsonl and dpo_candidates.jsonl.

Every string goes through the scrubber before it is written; an episode with
a secret anywhere in it is dropped whole, for every output.
"""
import copy
import glob
import hashlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from . import dpo, gitlabel, scrub, tools, transcripts

HEAD, TAIL = 1300, 600  # kept of a long tool result; ~2k chars with the marker


class Config:
    def __init__(self, raw, out, system_prompt, private=None, max_tokens=16000,
                 eval_pct=5, cap_per_rule=400, cap_per_session=15, sample=20, tasks=None):
        self.raw = Path(raw)
        self.out = Path(out)
        self.system_prompt = system_prompt
        self.private = private or {}
        self.max_tokens = max_tokens
        self.eval_pct = eval_pct
        self.cap_per_rule = cap_per_rule
        self.cap_per_session = cap_per_session
        self.sample = sample
        self.tasks = tasks  # evals/tasks dir: fix commits and holdout repos


def project_slug(dirname):
    s = re.sub(r"^-(?:Volumes-[^-]+|Users-[^-]+|private)-?", "", dirname)
    s = re.sub(r"^Dev-?", "", s)
    return s or "(root)"


def tokens(obj):
    # Rough: code and Czech run ~3 chars per token in the Qwen tokenizer.
    return len(json.dumps(obj, ensure_ascii=False)) // 3


def truncate(text):
    if len(text) <= HEAD + TAIL + 100:
        return text
    cut = len(text) - HEAD - TAIL
    return text[:HEAD] + f"\n[truncated {cut} chars]\n" + text[-TAIL:]


def finalize(messages):
    """Trims to a trajectory whose every call has a result and that ends with a plain answer.

    Returns the trimmed list, or None when no such prefix exists.
    """
    answered = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    for m in messages:
        if m["role"] == "assistant":
            for c in m.get("tool_calls") or []:
                if c["id"] not in answered:
                    # Everything from the unanswered call on is lost context.
                    messages = messages[:messages.index(m)]
                    break
            else:
                continue
            break
    while messages and not (messages[-1]["role"] == "assistant" and not messages[-1].get("tool_calls")
                            and messages[-1]["content"].strip()):
        messages = messages[:-1]
    if not messages or messages[0]["role"] != "user" or not any(m["role"] == "assistant" for m in messages):
        return None
    return messages


def _strings(m):
    yield m.get("content") or ""
    for c in m.get("tool_calls") or []:
        yield c["function"]["arguments"]


def _clean_value(s, v):
    if isinstance(v, str):
        return s.clean(v)
    if isinstance(v, dict):
        return {k: _clean_value(s, x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean_value(s, x) for x in v]
    return v


def scrub_messages(s, messages):
    """(cleaned copy, None) or (None, kind of the secret found)."""
    for m in messages:
        for text in _strings(m):
            kind = s.find_secret(text)
            if kind:
                return None, kind
    out = []
    for m in messages:
        m = copy.deepcopy(m)
        text = m.get("content") or ""
        # Truncate first: a cut through an e-mail would leave a fragment the cleaner misses.
        m["content"] = s.clean(truncate(text) if m["role"] == "tool" else text)
        for c in m.get("tool_calls") or []:
            args = json.loads(c["function"]["arguments"])
            c["function"]["arguments"] = tools.arguments_json(_clean_value(s, args))
        out.append(m)
    return out, None


def _supported(messages):
    return all(c["function"]["name"] in tools.TOOL_NAMES
               for m in messages if m["role"] == "assistant" for c in m.get("tool_calls") or [])


def make_scrubber(private, raw=None):
    names = set(private.get("names", []))
    for key in ("user.name", "user.email"):
        try:
            v = subprocess.run(["git", "config", "--global", key], capture_output=True, text=True).stdout.strip()
        except OSError:
            v = ""
        if key == "user.name" and v:
            names.add(v)
    env_files = []
    for g in private.get("env_globs", []):
        env_files += [p for p in glob.glob(os.path.expanduser(g)) if not p.endswith((".example", ".sample"))]
    values = scrub.env_secret_values(env_files) | set(private.get("secret_values", []))
    vols = set(scrub.local_volumes()) | set(private.get("volumes", []))
    return scrub.Scrubber(secret_values=values, names=names, volumes=vols,
                          name_stems=private.get("name_stems", []),
                          volume_words=private.get("volumes", []),
                          apple_ids=scrub.collect_apple_ids(sorted(Path(raw).rglob("*.jsonl"))) if raw else ()), len(env_files), len(values)


def _split(session, pct):
    return "eval" if int(hashlib.sha1(session.encode()).hexdigest()[:8], 16) % 100 < pct else "train"


def split_meta(ep, project, sub, cfg):
    # Per episode, not per session: a handful of long sessions hold most of the
    # data, so a session split cannot hit 5 %. Episodes start from a fresh
    # request, so they share little beyond the repo.
    return _split(ep.key, cfg.eval_pct), {
        "source": "claude-code-subagent" if sub else "claude-code", "project": project,
        "session": hashlib.sha1(ep.session.encode()).hexdigest()[:12]}


def _correction_pairs(s, cfg, system, ep, nxt, split_meta_, rows, stats, per_session_rule):
    """DPO pairs where the human objected and the next episode fixed what this one wrote."""
    split, meta = split_meta_
    rule = "in-session-correction"
    for i, chosen, rejected in dpo.correction_pairs(ep.messages, nxt.messages):
        prompt = ep.messages[:i]
        if not prompt or prompt[-1]["role"] not in ("user", "tool") or \
                not _supported(prompt + [rejected, chosen]):
            stats["correction_skipped"] += 1
            continue
        k = (meta["session"], rule)
        if per_session_rule[k] >= cfg.cap_per_session:
            stats["dpo_capped_" + rule] += 1
            continue
        clean, secret = scrub_messages(s, prompt + [rejected, chosen])
        if secret:
            stats["correction_secret"] += 1
            continue
        pair = {"prompt": [system] + clean[:-2], "chosen": [clean[-1]], "rejected": [clean[-2]],
                "tools": tools.TOOLS,
                "meta": {**meta, "category": "correction", "rule": rule,
                         "correction": s.clean(nxt.messages[0]["content"])[:300]}}
        if tokens(pair) > cfg.max_tokens:
            stats["dpo_too_long"] += 1
            continue
        per_session_rule[k] += 1
        stats["dpo_rule_" + rule] += 1
        rows[f"dpo_{split}"].append(pair)


def run(cfg):
    s, n_env, n_vals = make_scrubber(cfg.private, cfg.raw)
    system = {"role": "system", "content": cfg.system_prompt.strip()}
    stats = Counter()
    stats["env_files"], stats["secret_values"] = n_env, n_vals
    rows = defaultdict(list)  # output file -> rows
    per_rule = Counter()
    per_session_rule = Counter()
    czech_seen = Counter()

    # Excluded projects (client work, ...): dropped whole, and so is any episode
    # elsewhere that mentions them in a path, a cwd or plain text.
    words = [w for w in cfg.private.get("exclude", []) if w]
    excluded = re.compile("|".join(map(re.escape, words)), re.I) if words else None

    files = []
    for f in sorted(cfg.raw.rglob("*.jsonl")):
        rel = f.relative_to(cfg.raw)
        if excluded and excluded.search(rel.parts[0]):
            stats["files_excluded"] += 1
            continue
        files.append((f, project_slug(rel.parts[0]), "subagents" in rel.parts or len(rel.parts) > 2))

    # Pass 1: labels from git per (session, repo). The repo is found per
    # episode, since a session started in the dev root works in several.
    fixes, holdout = gitlabel.task_info(cfg.tasks) if cfg.tasks else (set(), set())
    stats["eval_fix_commits"], stats["eval_holdout_repos"] = len(fixes), len(holdout)
    evidence = defaultdict(lambda: {"commits": [], "times": []})
    roots, repo_of, start_root = {}, {}, {}
    for f, project, sub in files:
        for ep in transcripts.episodes(f, project):
            r = gitlabel.episode_repo(ep, roots)
            repo_of[(f, ep.index)] = r
            if not sub and ep.session not in start_root:
                start_root[ep.session] = gitlabel.repo_root(ep.cwd) if ep.cwd else None
            ev = evidence[(ep.session, r)]
            ev["commits"] += ep.commits
            ev["times"] += ep.commit_times
    git = gitlabel.Git()
    labels = {k: gitlabel.label_session(git, k[1], ev["commits"], ev["times"], fixes, holdout)
              for k, ev in evidence.items()}
    # Eval contamination drops the whole session when it was started in the
    # repo of a holdout task or of an eval fix it made. A session started in the dev root that
    # visited a holdout repo loses only the episodes there or naming it.
    # The same goes for an eval fix: in a dev-root session only the work in
    # the repo of the fix goes.
    tainted = {}
    for (sid, r), lab in labels.items():
        if lab in ("holdout", "eval_fix") and r and r == start_root.get(sid):
            tainted.setdefault(sid, lab)
    # Raw strings, not JSON: an escaped "\n" before a name would hide it from a word boundary.
    holdout_rx = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, sorted(holdout))) + r")(?![A-Za-z0-9])",
                            re.I) if holdout else None
    for v in labels.values():
        stats[f"session_repo_{v}"] += 1
    stats["sessions_tainted"] = len(tainted)

    for f, project, sub in files:
        stats["files_subagent" if sub else "files_main"] += 1
        eps = list(transcripts.episodes(f, project))
        for k, ep in enumerate(eps):
            stats["episodes"] += 1
            if ep.session in tainted:
                stats[f"drop_{tainted[ep.session]}"] += 1
                continue
            label = labels.get((ep.session, repo_of.get((f, ep.index))), "no_repo")
            if label == "eval_fix":
                stats["drop_eval_fix_episode"] += 1
                continue
            if label == "holdout" or (holdout_rx and any(holdout_rx.search(x) for m in ep.messages for x in _strings(m))):
                stats["drop_holdout_episode"] += 1
                continue
            if excluded and (excluded.search(json.dumps(ep.messages, ensure_ascii=False))
                             or any(excluded.search(r or "") for _, _, r in ep.denials)):
                stats["drop_excluded"] += 1
                continue
            corrected = gitlabel.is_correction(ep.next_human)
            nxt = eps[k + 1] if k + 1 < len(eps) else None
            if corrected and nxt is not None and not sub and \
                    not (excluded and excluded.search(json.dumps(nxt.messages, ensure_ascii=False))):
                _correction_pairs(s, cfg, system, ep, nxt, split_meta(ep, project, sub, cfg), rows,
                                  stats, per_session_rule)
            split, meta = split_meta(ep, project, sub, cfg)
            meta["session_label"] = label

            # Refused calls -> candidates (need a generated chosen turn).
            for ai, kind, reason in ep.denials:
                prompt = ep.messages[:ai]
                if not prompt or prompt[-1]["role"] not in ("user", "tool") or not _supported(prompt + [ep.messages[ai]]):
                    stats["denial_skipped"] += 1
                    continue
                clean, secret = scrub_messages(s, prompt + [ep.messages[ai]])
                if secret or s.find_secret(reason):
                    stats["denial_secret"] += 1
                    continue
                row = {"prompt": [system] + clean[:-1], "rejected": [clean[-1]], "need": "chosen",
                       "hint": truncate(s.clean(reason)), "tools": tools.TOOLS,
                       "meta": {**meta, "category": "refused-call", "rule": kind}}
                if tokens(row) > cfg.max_tokens:
                    stats["denial_too_long"] += 1
                    continue
                rows[f"dpo_candidates_{split}"].append(row)
                stats[f"candidate_refused_{kind}"] += 1

            if ep.unsupported:
                stats["drop_unsupported_tool"] += 1
                continue
            if ep.denials:
                stats["drop_has_denial"] += 1
                continue
            if ep.interrupted:
                stats["drop_interrupted"] += 1
                continue
            msgs = finalize(ep.messages)
            if msgs is None:
                stats["drop_incomplete"] += 1
                continue
            clean, secret = scrub_messages(s, msgs)
            if secret:
                stats["drop_secret"] += 1
                stats[f"secret_{secret}"] += 1
                continue
            n_calls = sum(len(m.get("tool_calls") or []) for m in clean)
            row = {"messages": [system] + clean, "tools": tools.TOOLS,
                   "meta": {**meta, "category": "agentic" if n_calls else "chat", "rule": None}}
            t = tokens(row)
            if label != "success":
                stats[f"sft_skip_{label}"] += 1
            elif corrected:
                stats["sft_skip_corrected"] += 1
            elif t > cfg.max_tokens:
                stats["drop_too_long"] += 1
            else:
                rows[f"sft_{split}"].append(row)
                stats[f"sft_{'le8k' if t <= 8000 else 'le16k'}"] += 1

            # Deterministic pairs from the same, already scrubbed trajectory.
            for i, rule, rej in dpo.deterministic_pairs(clean):
                k = (meta["session"], rule)
                if per_rule[rule] >= cfg.cap_per_rule or per_session_rule[k] >= cfg.cap_per_session:
                    stats[f"dpo_capped_{rule}"] += 1
                    continue
                pair = {"prompt": [system] + clean[:i], "chosen": [clean[i]], "rejected": [rej],
                        "tools": tools.TOOLS, "meta": {**meta, "category": "rule-mutation", "rule": rule}}
                if tokens(pair) > cfg.max_tokens:
                    stats["dpo_too_long"] += 1
                    continue
                per_rule[rule] += 1
                per_session_rule[k] += 1
                rows[f"dpo_{split}"].append(pair)

            last = clean[-1]
            if not corrected and dpo.is_czech_answer(last) and czech_seen["n"] < cfg.cap_per_rule and \
                    czech_seen[meta["session"]] < cfg.cap_per_session:
                cand = {"prompt": [system] + clean[:-1], "chosen": [last], "need": "rejected",
                        "hint": "Rewrite this answer in English, same content and length.",
                        "tools": tools.TOOLS, "meta": {**meta, "category": "language", "rule": "answer-in-czech"}}
                if tokens(cand) <= cfg.max_tokens:
                    czech_seen["n"] += 1
                    czech_seen[meta["session"]] += 1
                    rows[f"dpo_candidates_{split}"].append(cand)

    cfg.out.mkdir(parents=True, exist_ok=True)
    names = {"sft_train": "sft.jsonl", "sft_eval": "sft_eval.jsonl", "dpo_train": "dpo.jsonl",
             "dpo_eval": "dpo_eval.jsonl", "dpo_candidates_train": "dpo_candidates.jsonl",
             "dpo_candidates_eval": "dpo_candidates_eval.jsonl"}
    for key, fname in names.items():
        with open(cfg.out / fname, "w") as fh:
            for r in rows[key]:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        stats[f"out_{fname}"] = len(rows[key])
    for rule, n in per_rule.items():
        stats[f"dpo_rule_{rule}"] = n
    write_sample(cfg, rows)
    report = dict(sorted(stats.items()))
    (cfg.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def write_sample(cfg, rows):
    """A small, varied slice for a human and for the trainer's loader to check."""
    d = cfg.out / "sample"
    d.mkdir(exist_ok=True)
    n = cfg.sample
    sft = sorted(rows["sft_train"], key=tokens)
    pick = [sft[int(i * (len(sft) - 1) / max(1, n // 2 - 1))] for i in range(n // 2)] if sft else []
    by_rule = defaultdict(list)
    for r in rows["dpo_train"] + rows["dpo_candidates_train"]:
        by_rule[r["meta"]["rule"]].append(r)
    dpo_pick = []
    while len(dpo_pick) < n - len(pick) and any(by_rule.values()):
        for rule in sorted(by_rule):
            if by_rule[rule] and len(dpo_pick) < n - len(pick):
                lst = by_rule[rule]
                dpo_pick.append(lst.pop(len(lst) // 2))
    with open(d / "sft_sample.jsonl", "w") as fh:
        for r in pick:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(d / "dpo_sample.jsonl", "w") as fh:
        for r in dpo_pick:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
