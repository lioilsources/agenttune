"""Scrubbing: what may not leave the machine.

Two kinds of finding, handled differently:

- Identifying but harmless once replaced (home paths, volume names, LAN
  addresses, e-mails, the owner's name): replaced with a placeholder.
- A secret (a token, a key, a password, a value from a local .env): the whole
  episode is dropped. Masking a secret still teaches the model that secrets
  are printed and pasted around, and a mask that misses one character of a
  token leaks the rest.

The exact values (names, .env secrets) come from the local machine at run
time and are never written to the repo.
"""
import math
import os
import re
from collections import Counter

# --- replaced -----------------------------------------------------------

_HOME = re.compile(r"/(?:Users|home)/[^/\s\"'`:;,)\]]+")
_ENCODED_HOME = re.compile(r"-(?:Users|home)-[A-Za-z0-9_.]+-")
_SCRATCH = re.compile(r"/private/tmp/claude-\d+/[^\s\"'`]*?/scratchpad")
_TMP_CLAUDE = re.compile(r"/private/tmp/claude-\d+/[^\s\"'`/]+")
# Also regex-escaped addresses (name\\.x@host\\.com) as they appear in grep commands.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\*\.[A-Za-z]{2,}\b")
# Only well-known public senders; noreply@ on the owner's own domain still names the domain.
_EMAIL_OK = re.compile(r"^noreply@(?:anthropic\.com|github\.com)$|@users\.noreply\.github\.com$|@example\.(com|org)$")
_IPV4 = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w]|\.\d)")
# A private-range prefix in a template or loop (prefix + "$i" or "1xx"): the
# trailing dot is required so package versions like "10.7.0" stay.
_IPV4_PREFIX = re.compile(r"(?<![\w.])(?:10\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3})"
                          r"\.(?!\d{1,3}(?![\w.]))"
                          # 192.168.x alone is a network column in netstat; no version looks like it.
                          r"|(?<![\w.])192\.168\.\d{1,3}(?![\w.])")
_IP_OK = {"127.0.0.1", "0.0.0.0", "255.255.255.255", "255.255.255.0"}
_IPV6_ULA = re.compile(r"\bfd[0-9a-f]{2}:[0-9a-f:]{4,}", re.I)
_SESSION_URL = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9]+")
_ASC_KEY = re.compile(r"AuthKey_([A-Z0-9]{10})(?![A-Z0-9])")
_TEAM_ID = re.compile(r"(?i)(\b(?:development_?team|team_?id|teamid|key_?id)\b[\"']?\s*[:=]\s*[\"']?)([A-Z0-9]{10})\b")
_SSH_PUB = re.compile(r"\b(ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-nistp\d+) AAAA[A-Za-z0-9+/=]{20,}")
_ATTRIBUTION = re.compile(r"^.*(Co-Authored-By: Claude|Claude-Session:|Generated with \[Claude Code\]).*\n?", re.M | re.I)

# --- dropped ------------------------------------------------------------

_SECRET_PATTERNS = [
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{30,})")),
    ("api-key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("hf-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]
_BEARER = re.compile(r"Bearer\s+([A-Za-z0-9._~+/=-]{20,})")
_ASSIGN = re.compile(
    r"(?i)\b[\w-]*(?:token|secret|passw(?:or)?d|api[_-]?key|private[_-]?key|access[_-]?key|issuer[_-]?id)[\w-]*"
    r"[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-./+=]{16,})")
_URL_CRED = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@\"']+:([^\s/@\"']{6,})@")
_PLACEHOLDER = re.compile(r"^(?:\$|<|\{|%|x{3,}|\*{3,})|example|changeme|placeholder|redacted|your[_-]|dummy|\.\.\.",
                          re.I)


def entropy(s):
    c = Counter(s)
    return -sum(n / len(s) * math.log2(n / len(s)) for n in c.values())


def _looks_secret(v):
    if _PLACEHOLDER.search(v):
        return False
    return bool(re.search(r"\d", v)) and bool(re.search(r"[A-Za-z]", v)) and entropy(v) >= 3.5


_ENV_SECRET_KEY = re.compile(r"(?i)secret|token|key|pass|auth|cred|private|dsn|salt")


def env_secret_values(paths):
    """Values of secret-looking keys in local .env files, for exact matching."""
    out = set()
    for p in paths:
        try:
            with open(p, errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.removeprefix("export ").strip()
            v = v.strip().strip("\"'")
            if len(v) >= 10 and _ENV_SECRET_KEY.search(k) and not _PLACEHOLDER.search(v):
                out.add(v)
    return out


class Scrubber:
    def __init__(self, secret_values=(), names=(), volumes=(), name_stems=(), volume_words=(), apple_ids=()):
        vals = sorted({v for v in secret_values if len(v) >= 10}, key=len, reverse=True)
        self._exact = re.compile("|".join(map(re.escape, vals))) if vals else None
        names = sorted({n for n in names if len(n) >= 3}, key=len, reverse=True)
        self._names = re.compile(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b", re.I) if names else None
        # Apple key and team ids found anywhere in the raw data, replaced even
        # where they stand alone ("5YH..." in a dict next to the key file).
        ids = sorted({i for i in apple_ids if len(i) == 10}, reverse=True)
        self._apple = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(ids) + r")(?![A-Za-z0-9])") if ids else None
        stems = sorted({n for n in name_stems if len(n) >= 4}, key=len, reverse=True)
        # Stems match inside words too: a surname stem in a grep pattern is still the name.
        self._stems = re.compile("|".join(map(re.escape, stems)), re.I) if stems else None
        vols = sorted({v for v in volumes if v and v != "Macintosh HD"}, key=len, reverse=True)
        self._vol_paths = re.compile(r"/Volumes/(?:" + "|".join(map(re.escape, vols)) + r"|[^/\s\"'`]+)") \
            if vols else re.compile(r"/Volumes/[^/\s\"'`]+")
        self._vol_encoded = re.compile(r"-Volumes-(?:" + "|".join(re.escape(v.replace(" ", "-")) for v in vols) +
                                       r"|[A-Za-z0-9]+)-")
        # Bare words only for the distinctive names listed explicitly: a volume
        # called "pool" or "backup" must not rewrite every pool and backup.
        words = sorted({w for w in volume_words if w}, key=len, reverse=True)
        self._vol_words = re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b", re.I) if words else None

    def find_secret(self, text):
        """The kind of the first secret in text, or None."""
        if self._exact and self._exact.search(text):
            return "env-value"
        for kind, rx in _SECRET_PATTERNS:
            if rx.search(text):
                return kind
        for m in _BEARER.finditer(text):
            if _looks_secret(m.group(1)):
                return "bearer"
        for m in _ASSIGN.finditer(text):
            if _looks_secret(m.group(1)):
                return "assignment"
        for m in _URL_CRED.finditer(text):
            if not _PLACEHOLDER.search(m.group(1)):
                return "url-credential"
        return None

    def clean(self, text):
        text = _ATTRIBUTION.sub("", text)
        text = _SESSION_URL.sub("<SESSION_URL>", text)
        text = _ASC_KEY.sub("AuthKey_<KEY_ID>", text)
        if self._apple:
            text = self._apple.sub("<APPLE_ID>", text)
        text = _TEAM_ID.sub(lambda m: m.group(1) + "<TEAM_ID>", text)
        text = _SSH_PUB.sub(lambda m: m.group(1) + " <SSH_PUBKEY>", text)
        text = _SCRATCH.sub("/tmp/scratchpad", text)
        text = _TMP_CLAUDE.sub("/tmp/claude", text)
        text = self._vol_paths.sub("/work", text)
        text = self._vol_encoded.sub("-work-", text)
        text = _HOME.sub("~", text)
        text = _ENCODED_HOME.sub("-home-", text)
        if self._vol_words:
            text = self._vol_words.sub("<VOLUME>", text)
        text = _EMAIL.sub(lambda m: m.group(0) if _EMAIL_OK.search(m.group(0)) else "<EMAIL>", text)
        text = _IPV4.sub(_ip, text)
        text = _IPV4_PREFIX.sub(lambda m: "<IP>." if m.group(0).endswith(".") else "<IP>", text)
        text = _IPV6_ULA.sub("<IP6>", text)
        if self._names:
            text = self._names.sub("<NAME>", text)
        if self._stems:
            text = self._stems.sub("<NAME>", text)
        return text


def _ip(m):
    if any(int(x) > 255 for x in m.groups()):
        return m.group(0)
    ip = m.group(0)
    return ip if ip in _IP_OK else "<IP>"


def collect_apple_ids(paths):
    """Apple key/team ids mentioned anywhere in the given files."""
    found = set()
    for p in paths:
        with open(p, errors="replace") as fh:
            for line in fh:
                if "AuthKey_" in line or "eam" in line or "ey_id" in line.lower():
                    found.update(_ASC_KEY.findall(line))
                    found.update(m.group(2) for m in _TEAM_ID.finditer(line)
                                 if re.search(r"\d", m.group(2)) and re.search(r"[A-Z]", m.group(2)))
    return found


def local_volumes():
    try:
        return [v for v in os.listdir("/Volumes") if not v.startswith(".")]
    except OSError:
        return []
