"""Preference pairs.

Two sources:

1. Deterministic: a real assistant turn that followed a rule is `chosen`; the
   same turn with exactly that rule broken is `rejected`. Nothing else
   differs, so the pair teaches the decision and not length or style.
2. Candidates for synthesis (synth_dpo.py, runs on the training box):
   - a call that a hook, the safety classifier or the owner refused: the
     real turn is `rejected`, the `chosen` turn is generated;
   - a final answer in Czech: the real turn is `chosen`, an English
     rewrite of the same answer is generated as `rejected`.
"""
import copy
import json
import re

from . import rules

# rule id -> (pattern a compliant command matches, rewrite that breaks the rule)
_ADD = re.compile(r"\bgit add +((?!-A\b|--all\b|\.(?:\s|$)|-p\b|-u\b)[^;&|\n]*?)(?=\s*(?:&&|;|\||\n|$))")
_PUSH = re.compile(r"\bgit push\b(?![^;&|\n]*(?:--force|\s-f\b))")
_COMMIT = re.compile(r"\bgit commit\b(?![^;&|\n]*--no-verify)")


def _bash(fn):
    def f(name, args):
        if name != "bash":
            return None
        cmd = args.get("command", "")
        new = fn(cmd)
        return dict(args, command=new) if new and new != cmd else None
    return f


@_bash
def _mut_add(cmd):
    m = _ADD.search(cmd)
    if not m or not m.group(1).strip():
        return None
    return cmd[:m.start(1)] + "-A" + cmd[m.end(1):]


@_bash
def _mut_push(cmd):
    return _PUSH.sub("git push --force", cmd, count=1) if _PUSH.search(cmd) else None


@_bash
def _mut_commit(cmd):
    return _COMMIT.sub("git commit --no-verify", cmd, count=1) if _COMMIT.search(cmd) else None


_WRAPPED = re.compile(r'return\s+((?:[^,\n()]+,\s*)*)fmt\.Errorf\("[^"\n]*%w[^"\n]*",\s*err\)')


def _mut_unwrap(name, args):
    """Unwrap the first fmt.Errorf("…: %w", err) the eval would then flag as a bare return."""
    if name not in ("edit", "write"):
        return None
    path = args.get("filePath") or ""
    key = "newString" if name == "edit" else "content"
    text = args.get(key) or ""
    if not rules.is_go(path) or rules.is_go_test(path) or rules.go_errors_wrap(text):
        return None
    for m in _WRAPPED.finditer(text):
        new = text[:m.start()] + "return " + m.group(1) + "err" + text[m.end():]
        if rules.go_errors_wrap(new):
            return dict(args, **{key: new})
    return None


_GO_CHECK = re.compile(r"\bgo (?:test|build|vet)\b")
_FLUTTER_CHECK = re.compile(r"\bflutter (?:test|analyze)\b")


@_bash
def _mut_deps(cmd):
    if rules.DEP_CMD.search(cmd):
        return None
    for rx, up in ((_GO_CHECK, "go get -u ./... && "), (_FLUTTER_CHECK, "flutter pub upgrade --major-versions && ")):
        m = rx.search(cmd)
        if m:
            return cmd[:m.start()] + up + cmd[m.start():]
    return None


MUTATIONS = {
    "git-add-by-name": _mut_add,      # git.md: no git add -A / . / --all
    "no-force-push": _mut_push,       # git.md: no force push
    "no-hook-bypass": _mut_commit,    # public-repo.md: the pre-commit hook is the leak guard
    "go-errors-wrap": _mut_unwrap,    # stacks/go.md: wrap errors from other packages
    "no-new-deps": _mut_deps,         # agenttune plan F1: no new or upgraded deps unasked
}

_CZECH = re.compile(r"[ěščřžýáíéůúťďňĚŠČŘŽÝÁÍÉŮÚŤĎŇ]")


def mutate(msg, rule):
    """A copy of an assistant message with `rule` broken in its first call it applies to, or None."""
    fn = MUTATIONS[rule]
    out = copy.deepcopy(msg)
    for call in out.get("tool_calls") or []:
        try:
            args = json.loads(call["function"]["arguments"])
        except (TypeError, ValueError):
            continue
        new = fn(call["function"]["name"], args) if isinstance(args, dict) else None
        if new is not None:
            call["function"]["arguments"] = json.dumps(new, ensure_ascii=False)
            return out
    return None


def deterministic_pairs(messages):
    """(index, rule, rejected message) for every assistant turn a mutation applies to."""
    for i, m in enumerate(messages):
        if m["role"] != "assistant" or not m.get("tool_calls") or i == 0:
            continue
        if messages[i - 1]["role"] not in ("user", "tool"):
            continue
        for rule in MUTATIONS:
            rej = mutate(m, rule)
            if rej is not None:
                yield i, rule, rej


def is_czech_answer(msg):
    t = msg.get("content") or ""
    return msg["role"] == "assistant" and not msg.get("tool_calls") and len(t) >= 80 and \
        len(_CZECH.findall(t)) >= 5


def _edit_calls(messages):
    """(message index, call index, name, args) for every write/edit call."""
    for i, m in enumerate(messages):
        for j, c in enumerate(m.get("tool_calls") or []):
            if c["function"]["name"] in ("edit", "write"):
                yield i, j, c["function"]["name"], json.loads(c["function"]["arguments"])


def correction_pairs(first, follow):
    """Pairs from an in-session correction.

    `first` is an episode the human objected to, `follow` the episode that
    fixed it. Where `follow` edits text that `first` had just written (its
    oldString lies inside a newString or content of `first`), rejected is
    the original call and chosen the same call with the fix applied. Yields
    (message index in first, chosen message, rejected message).
    """
    fixes = [(a["filePath"], a.get("oldString"), a.get("newString") or "")
             for _, _, name, a in _edit_calls(follow) if name == "edit"]
    for i, j, name, a in _edit_calls(first):
        key = "newString" if name == "edit" else "content"
        orig = a.get(key) or ""
        fixed = orig
        for path, old, new in fixes:
            if path == a["filePath"] and old and old in fixed:
                fixed = fixed.replace(old, new, 1)
        if fixed == orig or not orig.strip():
            continue
        chosen = copy.deepcopy(first[i])
        chosen["tool_calls"][j]["function"]["arguments"] = json.dumps(dict(a, **{key: fixed}), ensure_ascii=False)
        yield i, chosen, first[i]
