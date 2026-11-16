"""The six tools every example declares, and the mapping from Claude Code's.

Every row carries the same list so the chat template renders the same system
prompt for all of them. A Claude Code tool outside this mapping (Agent,
SendMessage, MCP, ...) makes the episode unusable for SFT: dropping the call
would leave a trajectory that reacts to something that never happened.
"""
import json


def _fn(name, description, props, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}

TOOLS = [
    _fn("read_file", "Read a text file. Returns lines prefixed with their line numbers.",
        {"path": _S, "offset": _I, "limit": _I}, ["path"]),
    _fn("write_file", "Create or overwrite a file with the given content.",
        {"path": _S, "content": _S}, ["path", "content"]),
    _fn("edit_file", "Replace an exact string in a file. old_string must be unique unless replace_all.",
        {"path": _S, "old_string": _S, "new_string": _S, "replace_all": _B},
        ["path", "old_string", "new_string"]),
    _fn("bash", "Run a shell command and return its output.",
        {"command": _S, "timeout": _I}, ["command"]),
    _fn("grep", "Search file contents with a regular expression.",
        {"pattern": _S, "path": _S, "glob": _S}, ["pattern"]),
    _fn("glob", "List files matching a glob pattern.",
        {"pattern": _S, "path": _S}, ["pattern"]),
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}

# Claude Code name -> (our name, {their arg: our arg})
_MAP = {
    "Read": ("read_file", {"file_path": "path", "offset": "offset", "limit": "limit"}),
    "Write": ("write_file", {"file_path": "path", "content": "content"}),
    "Edit": ("edit_file", {"file_path": "path", "old_string": "old_string",
                           "new_string": "new_string", "replace_all": "replace_all"}),
    "Bash": ("bash", {"command": "command", "timeout": "timeout"}),
    "bash": ("bash", {"command": "command", "timeout": "timeout"}),
    "Grep": ("grep", {"pattern": "pattern", "path": "path", "glob": "glob"}),
    "Glob": ("glob", {"pattern": "pattern", "path": "path"}),
}


def convert_call(name, inp):
    """Returns (name, args dict) in our schema, or None for an unsupported tool."""
    m = _MAP.get(name)
    if m is None or not isinstance(inp, dict):
        return None
    ours, argmap = m
    args = {argmap[k]: v for k, v in inp.items() if k in argmap and v is not None}
    if ours == "bash" and "command" not in args:
        return None
    return ours, args


def arguments_json(args):
    return json.dumps(args, ensure_ascii=False)
