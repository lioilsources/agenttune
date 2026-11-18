#!/usr/bin/env python3
"""Complete DPO candidates with a generated turn; runs on the training box.

    python3 data/synth_dpo.py dpo_candidates.jsonl dpo_synth.jsonl \
        --base-url http://localhost:8000/v1 --model qwen3-coder

Input is the scrubbed dpo_candidates.jsonl from build.py; no raw transcript
is needed or read. A candidate has either `rejected` (a refused call; we
generate `chosen`) or `chosen` (a Czech answer; we generate the English
`rejected`). The generated turn must be one assistant message that differs
from the given one only in that decision. Rows the model answers badly are
skipped and counted, never guessed.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentdata import tools  # noqa: E402

CHOSEN_TASK = """The last assistant turn below was refused. Refusal message:
---
{hint}
---
Write the assistant turn that should have been made instead, in the same
context: follow the rule the refusal enforces, do not work around it. If the
action needs the owner, say briefly in Czech what was blocked and give the
command the owner can run themselves (prefixed with `! `). Keep the length and
style of the refused turn; change only the decision.

Refused turn (JSON):
{given}
"""

REJECTED_TASK = """Rewrite the assistant answer below in English. Keep every
fact, the structure and the length; change only the language.

Answer (JSON):
{given}
"""

FORMAT = """Return only a JSON object: {"content": "<text>", "tool_calls": [{"name": "<tool>", "arguments": {...}}]}.
tool_calls may be empty. Allowed tools: bash, read, edit, write, grep, glob."""


def render_context(prompt, limit=12000):
    lines = []
    for m in prompt:
        if m["role"] == "system":
            continue
        body = m.get("content") or ""
        for c in m.get("tool_calls") or []:
            body += f"\n[call {c['function']['name']} {c['function']['arguments']}]"
        lines.append(f"## {m['role']}\n{body}")
    text = "\n\n".join(lines)
    return text[-limit:]


def task_for(row):
    given = row["rejected"][0] if row["need"] == "chosen" else row["chosen"][0]
    g = json.dumps({"content": given.get("content", ""),
                    "tool_calls": [{"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"])}
                                   for c in given.get("tool_calls") or []]}, ensure_ascii=False)
    tpl = CHOSEN_TASK if row["need"] == "chosen" else REJECTED_TASK
    return tpl.format(hint=row.get("hint", ""), given=g) + "\n" + FORMAT


_REQUIRED = {t["function"]["name"]: t["function"]["parameters"]["required"] for t in tools.TOOLS}


def parse_turn(text):
    """The model's JSON answer as an assistant message, or None."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        o = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(o, dict) or not isinstance(o.get("content", ""), str):
        return None
    msg = {"role": "assistant", "content": o.get("content", "")}
    calls = []
    for i, c in enumerate(o.get("tool_calls") or []):
        if not isinstance(c, dict) or c.get("name") not in tools.TOOL_NAMES or not isinstance(c.get("arguments"), dict):
            return None
        if c["name"] == "bash":
            c["arguments"].setdefault("description", "Run command")
        if any(k not in c["arguments"] for k in _REQUIRED[c["name"]]):
            return None
        calls.append({"id": f"call_s{i + 1}", "type": "function",
                      "function": {"name": c["name"], "arguments": tools.arguments_json(c["arguments"])}})
    if calls:
        msg["tool_calls"] = calls
    if not msg["content"].strip() and not calls:
        return None
    return msg


def complete(row, gen):
    out = gen(render_context(row["prompt"]), task_for(row))
    turn = parse_turn(out or "")
    if turn is None:
        return None
    pair = {"prompt": row["prompt"], "tools": row["tools"], "meta": {**row["meta"], "synthetic": row["need"]}}
    if row["need"] == "chosen":
        pair["chosen"], pair["rejected"] = [turn], row["rejected"]
    else:
        pair["chosen"], pair["rejected"] = row["chosen"], [turn]
    if pair["chosen"][0] == pair["rejected"][0]:
        return None
    return pair


def openai_gen(base_url, model, api_key="none"):
    def gen(context, task):
        body = json.dumps({"model": model, "temperature": 0.3, "max_tokens": 2048, "messages": [
            {"role": "system", "content": "You write training data for a coding agent. Follow the format exactly."},
            {"role": "user", "content": "Conversation so far:\n\n" + context + "\n\n# Task\n" + task},
        ]}).encode()
        req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    return gen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates")
    ap.add_argument("out")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    a = ap.parse_args()
    gen = openai_gen(a.base_url, a.model)
    ok = bad = 0
    with open(a.candidates) as fin, open(a.out, "w") as fout:
        for line in fin:
            try:
                pair = complete(json.loads(line), gen)
            except (OSError, ValueError, KeyError):
                pair = None
            if pair is None:
                bad += 1
                continue
            ok += 1
            fout.write(json.dumps(pair, ensure_ascii=False) + "\n")
    print(json.dumps({"ok": ok, "skipped": bad}))


if __name__ == "__main__":
    main()
