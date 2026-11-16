"""Reads Claude Code session transcripts and cuts them into episodes.

An episode is one request and everything the agent did for it: it starts at a
message from the human (or from a peer session, which is a request too) and
ends right before the next one. Thinking blocks are dropped, since the target
model does not think out loud.

A transcript line is one JSON object. Only "user" and "assistant" lines carry
the conversation; one assistant API response is split over several lines
sharing message.id, one per content block.
"""
import json
import re
from dataclasses import dataclass, field

from . import tools

# Denials that mean "this was the wrong call": a rule hook or the safety
# classifier refused it, or the human rejected it. A classifier outage
# (automode-unavailable) says nothing about the call, and a cancelled prompt
# is a "not now", not a "no".
DPO_DENIALS = {"automode-blocked", "permission-rule", "user-rejected"}

# Inputs that start an episode. task-notification, scheduled and system turns
# are events inside the work, not new requests.
_STARTS = {"human", "peer", None}

# User strings that are UI chrome, not something anyone typed.
_SKIP_PREFIXES = ("<command-name", "<command-message", "<local-command-", "<bash-input",
                  "<bash-stdout", "<bash-stderr", "<artifact-view-context", "Caveat:")

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.S)
_WRAPPERS = re.compile(
    r"</?(pasted_content|cross-session-message|task-notification|artifact-content-authored-by-others)\b[^>]*>")
_INTERRUPT = "[Request interrupted"


@dataclass
class Episode:
    session: str
    project: str
    index: int
    messages: list = field(default_factory=list)  # OpenAI-style, no system message
    # (index of the assistant message, denial kind, reason text) per refused call
    denials: list = field(default_factory=list)
    unsupported: set = field(default_factory=set)  # Claude Code tools outside our six
    interrupted: bool = False

    @property
    def key(self):
        return f"{self.session}#{self.index}"


def clean_input(text):
    """Strips reminders and wrapper tags from a user string; None if it is UI chrome."""
    text = _REMINDER.sub("", text).strip()
    if not text or text.startswith(_SKIP_PREFIXES):
        return None
    return _WRAPPERS.sub("", text).strip() or None


def _origin(o):
    for k in ("origin", "turnOrigin"):
        v = o.get(k)
        if isinstance(v, dict):
            return v.get("kind")
        if isinstance(v, str):
            return v
    return None


def _result_text(content):
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "image":
                parts.append("[image]")
    return "\n".join(parts)


def read_lines(path):
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict) and o.get("type") in ("user", "assistant"):
                yield o


def episodes(path, project):
    """Yields the episodes of one transcript file, in order."""
    session = None
    cur = None
    n = 0
    last_asst_id = None
    call_ids = {}  # Claude tool_use id -> (our call id, assistant message index)

    def close():
        nonlocal cur
        done, cur = cur, None
        return done

    for o in read_lines(path):
        session = session or o.get("sessionId") or str(path)
        if o.get("isMeta") or o.get("isApiErrorMessage") or o.get("isVisibleInTranscriptOnly"):
            continue
        if o.get("isCompactSummary"):
            # The context the agent worked from just changed under it.
            if cur:
                yield close()
            continue
        msg = o.get("message") or {}
        content = msg.get("content")

        if o["type"] == "assistant":
            if cur is None or not isinstance(content, list):
                continue
            mid = msg.get("id")
            if mid is None or mid != last_asst_id or not cur.messages or cur.messages[-1]["role"] != "assistant":
                cur.messages.append({"role": "assistant", "content": ""})
                last_asst_id = mid
            a = cur.messages[-1]
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    a["content"] = (a["content"] + "\n\n" + b["text"]).strip()
                elif b.get("type") == "tool_use":
                    conv = tools.convert_call(b.get("name", ""), b.get("input"))
                    if conv is None:
                        cur.unsupported.add(b.get("name", "?"))
                        conv = (b.get("name", "?"), b.get("input") or {})
                    cid = f"call_{len(call_ids) + 1}"
                    call_ids[b.get("id")] = (cid, len(cur.messages) - 1)
                    a.setdefault("tool_calls", []).append({
                        "id": cid, "type": "function",
                        "function": {"name": conv[0], "arguments": tools.arguments_json(conv[1])},
                    })
            continue

        # user line
        last_asst_id = None
        if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            if cur is None:
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and _INTERRUPT in b.get("text", ""):
                    cur.interrupted = True
                if b.get("type") != "tool_result":
                    continue
                cid, ai = call_ids.get(b.get("tool_use_id"), (None, None))
                if cid is None:
                    continue
                text = _REMINDER.sub("", _result_text(b.get("content"))).strip()
                if b.get("is_error"):
                    kind = o.get("toolDenialKind")
                    if kind in DPO_DENIALS:
                        cur.denials.append((ai, kind, text))
                    text = "Error: " + text
                cur.messages.append({"role": "tool", "tool_call_id": cid, "content": text})
            continue

        if isinstance(content, list):
            text = "\n".join(_result_text([b]) for b in content if isinstance(b, dict))
        else:
            text = content if isinstance(content, str) else ""
        if _INTERRUPT in text:
            if cur:
                cur.interrupted = True
                yield close()
            continue
        text = clean_input(text)
        if text is None:
            continue
        if _origin(o) in _STARTS:
            if cur:
                yield close()
            cur = Episode(session=session, project=project, index=n)
            call_ids = {}
            n += 1
        elif cur is None:
            continue
        cur.messages.append({"role": "user", "content": text})

    if cur:
        yield close()
