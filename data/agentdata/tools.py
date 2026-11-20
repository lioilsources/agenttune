"""The six OpenCode tools every example declares, and the mapping from Claude Code's.

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

# OpenCode's built-in tools, the harness the tuned model runs in (evals/harness):
# the same names and argument names, so nothing is lost between training and use.
TOOLS = [
    _fn("bash", "Run a shell command and return its output.",
        {"command": _S, "description": _S, "timeout": _I, "workdir": _S}, ["command", "description"]),
    _fn("read", "Read a text file. Returns lines prefixed with their line numbers.",
        {"filePath": _S, "offset": _I, "limit": _I}, ["filePath"]),
    _fn("edit", "Replace an exact string in a file. oldString must be unique unless replaceAll.",
        {"filePath": _S, "oldString": _S, "newString": _S, "replaceAll": _B},
        ["filePath", "oldString", "newString"]),
    _fn("write", "Create or overwrite a file with the given content.",
        {"filePath": _S, "content": _S}, ["filePath", "content"]),
    _fn("grep", "Search file contents with a regular expression.",
        {"pattern": _S, "path": _S, "include": _S}, ["pattern"]),
    _fn("glob", "List files matching a glob pattern.",
        {"pattern": _S, "path": _S}, ["pattern"]),
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
WRITE_TOOLS = {"edit", "write"}

# Claude Code name -> (our name, {their arg: our arg})
_MAP = {
    "Read": ("read", {"file_path": "filePath", "offset": "offset", "limit": "limit"}),
    "Write": ("write", {"file_path": "filePath", "content": "content"}),
    "Edit": ("edit", {"file_path": "filePath", "old_string": "oldString",
                      "new_string": "newString", "replace_all": "replaceAll"}),
    "Bash": ("bash", {"command": "command", "description": "description", "timeout": "timeout"}),
    "bash": ("bash", {"command": "command", "description": "description", "timeout": "timeout"}),
    "Grep": ("grep", {"pattern": "pattern", "path": "path", "glob": "include"}),
    "Glob": ("glob", {"pattern": "pattern", "path": "path"}),
}


def convert_call(name, inp):
    """Returns (name, args dict) in our schema, or None for an unsupported tool."""
    m = _MAP.get(name)
    if m is None or not isinstance(inp, dict):
        return None
    ours, argmap = m
    args = {argmap[k]: v for k, v in inp.items() if k in argmap and v is not None}
    if ours == "bash":
        if "command" not in args:
            return None
        args.setdefault("description", "Run command")  # required in OpenCode
    return ours, args


def arguments_json(args):
    return json.dumps(args, ensure_ascii=False)
