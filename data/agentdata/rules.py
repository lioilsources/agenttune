"""Rule checks on the code a trajectory writes, mirroring agenttune-eval.

The eval scores a run by the diff it leaves; here the same checks run over
the text of each edit/write call, so an SFT trajectory that would be marked
as a violation in the eval is not taught as a good example. An edit's
newString holds context lines as well as added ones, so this errs on the
side of dropping.
"""
import json
import posixpath
import re

# Same patterns as internal/rules/rules.go in agenttune-eval.
BARE_ERR_RETURN = re.compile(r"^\s*return\s+(?:[^,]+,\s*)*err\s*$")
ERR_ASSIGN = re.compile(r"\berr\s*:?=\s*(.*)$")
QUALIFIED_CALL = re.compile(r"^\s*&?[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*\.[A-Za-z_][a-zA-Z0-9_]*\(")
DART_DEP = re.compile(r"^\s{2}[a-z0-9_]+:")
# Commands that add or upgrade dependencies.
DEP_CMD = re.compile(r"\b(?:go get\b|flutter pub (?:add|upgrade)\b|dart pub add\b|npm (?:install|i|add) +[^-\s]|"
                     r"pnpm add\b|yarn add\b|pip install +[^-\s])")


def _strip_comment(s):
    i = s.find("//")
    return s[:i] if i >= 0 else s


def is_go(p):
    return p.endswith(".go")


def is_go_test(p):
    return p.endswith("_test.go")


def _generated(text):
    head = text[:2048]
    return "Code generated" in head and "DO NOT EDIT" in head


def go_errors_wrap(text):
    """Lines with a bare `return …, err` whose err came from a qualified call."""
    out, origin = [], ""
    for n, raw in enumerate(text.splitlines(), 1):
        s = _strip_comment(raw)
        m = ERR_ASSIGN.search(s)
        if m and "fmt.Errorf" not in s:
            origin = m.group(1)
            continue
        if BARE_ERR_RETURN.match(s) and QUALIFIED_CALL.match(origin):
            out.append(n)
    return out


def _go_mod_deps(text):
    out = []
    for raw in text.splitlines():
        t = raw.strip()
        if t.startswith("require ") or (" v" in t and not t.startswith(("go ", "module ")) and
                                        not t.endswith("// indirect")):
            out.append(t)
    return out


def _pubspec_deps(text):
    out, sec = [], ""
    for raw in text.splitlines():
        m = re.match(r"^([a-z_]+):", raw)
        if m:
            sec = m.group(1)
        elif DART_DEP.match(raw) and sec in ("dependencies", "dev_dependencies"):
            out.append(raw.strip())
    return out


def call_violations(name, args):
    """Rule ids one tool call breaks."""
    out = set()
    if name == "bash":
        if DEP_CMD.search(args.get("command") or ""):
            out.add("no-new-deps")
        return out
    if name not in ("edit", "write"):
        return out
    path = args.get("filePath") or ""
    text = args.get("newString" if name == "edit" else "content") or ""
    base = posixpath.basename(path)
    if is_go(path) and not is_go_test(path) and not _generated(text) and go_errors_wrap(text):
        out.add("go-errors-wrap")
    if base == "go.mod" and _go_mod_deps(text):
        out.add("no-new-deps")
    if base == "pubspec.yaml" and _pubspec_deps(text):
        out.add("no-new-deps")
    return out


def violations(messages):
    """Rule ids any assistant call in the trajectory breaks."""
    out = set()
    for m in messages:
        if m["role"] != "assistant":
            continue
        for c in m.get("tool_calls") or []:
            try:
                args = json.loads(c["function"]["arguments"])
            except (TypeError, ValueError):
                continue
            if isinstance(args, dict):
                out |= call_violations(c["function"]["name"], args)
    return out
