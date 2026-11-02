"""Inventory of Claude Code session transcripts: counts only, no content is printed."""
import json, sys, pathlib, collections

root = pathlib.Path(sys.argv[1]).expanduser()
per = collections.defaultdict(lambda: collections.Counter())
for f in root.rglob("*.jsonl"):
    rel = f.relative_to(root)
    proj = rel.parts[0]
    side = "subagents" in rel.parts or len(rel.parts) > 2
    c = per[proj]
    c["subagent_files" if side else "sessions"] += 1
    c["bytes"] += f.stat().st_size
    human = tool = asst = 0
    with f.open(errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            t = o.get("type")
            msg = o.get("message") or {}
            if t == "user" and (o.get("origin") or {}).get("kind") == "human" and isinstance(msg.get("content"), str):
                human += 1
            elif t == "user" and not o.get("origin") and isinstance(msg.get("content"), str):
                human += 1
            elif t == "assistant":
                asst += 1
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tool += 1
    if side:
        c["sub_tool_calls"] += tool
        continue
    c["human_turns"] += human
    c["assistant_msgs"] += asst
    c["tool_calls"] += tool
    if human >= 3:
        c["sessions_ge3_turns"] += 1
    if tool:
        c["sessions_with_tools"] += 1

keys = ["sessions", "sessions_ge3_turns", "sessions_with_tools", "human_turns", "tool_calls", "subagent_files", "sub_tool_calls", "bytes"]
tot = collections.Counter()
print("| projekt | " + " | ".join(keys) + " |")
print("|---" * (len(keys) + 1) + "|")
for p, c in sorted(per.items(), key=lambda kv: -kv[1]["human_turns"]):
    tot.update(c)
    name = p.rsplit("-Dev-", 1)[-1]  # strip the encoded local path prefix
    print(f"| {name} | " + " | ".join(str(c[k]) for k in keys) + " |")
print("| **celkem** | " + " | ".join(str(tot[k]) for k in keys) + " |")
