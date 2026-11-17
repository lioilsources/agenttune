"""Session labels from git: was the work kept, and does it touch an eval task?

A session is "success" when at least one of its commits is still reachable
from a branch and has not been reverted. Commits are found two ways: the
"[branch abc1234]" line `git commit` prints, and, because most commits run
with -q, any commit of the repo whose committer time lies within SLACK
seconds of a successful `git commit` result.

Eval contamination: sessions in a holdout task's repo, and sessions that made
the fix commit of any eval task, are dropped from every output.
"""
import datetime
import glob
import os
import re
import subprocess

SLACK = 180

# A short next human message matching this means the episode was not right.
CORRECTION = re.compile(
    r"(?i)(^\s*ne[\s,.!]|^\s*ne$|nefunguj|špatn|spatn|chyb[aiu]|nesed[ií]|rozbil|rozbit|"
    r"^\s*oprav|proč\s+(jsi|to)|to\s+není|neni\s+to|tohle\s+ne|vrať|zase\s|pořád\s+ne|"
    r"wrong|doesn'?t\s+work|broken|^\s*revert|not\s+what)")


def is_correction(text):
    return bool(text) and len(text) < 600 and bool(CORRECTION.search(text))


def task_info(tasks_dir):
    """(fix commits of all eval tasks, lower-case repo names of holdout tasks)."""
    fixes, holdout = set(), set()
    for f in glob.glob(os.path.join(tasks_dir, "*", "task.yaml")):
        t = {}
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^(repo|fix|split):\s*(\S+)", line)
                if m:
                    t[m.group(1)] = m.group(2).strip("\"'")
        if t.get("fix"):
            fixes.add(t["fix"].lower())
        if t.get("split") == "holdout" and t.get("repo"):
            holdout.add(t["repo"].split("/")[-1].lower())
    return fixes, holdout


def repo_root(path):
    """Nearest ancestor with a .git entry (a worktree has a .git file)."""
    p = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def _ts(iso):
    return int(datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


class Git:
    """Cached git queries, one repo at a time."""

    def __init__(self):
        self._log, self._state, self._names = {}, {}, {}

    def _run(self, root, *args):
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
        return p.returncode, p.stdout

    def names(self, root):
        """Lower-case names the repo goes by: its directory and its origin."""
        if root not in self._names:
            n = {os.path.basename(root).lower()}
            rc, url = self._run(root, "remote", "get-url", "origin")
            if rc == 0 and url.strip():
                n.add(re.sub(r"\.git$", "", url.strip().rstrip("/")).split("/")[-1].split(":")[-1].lower())
            self._names[root] = n
        return self._names[root]

    def near(self, root, times):
        """Full hashes of commits within SLACK seconds of any of times."""
        if not times:
            return set()
        if root not in self._log:
            _, out = self._run(root, "log", "--all", "--format=%H %ct")
            self._log[root] = [(h, int(t)) for h, t in (x.split() for x in out.splitlines() if x.strip())]
        ts = [_ts(t) for t in times]
        return {h for h, ct in self._log[root] if any(abs(ct - t) <= SLACK for t in ts)}

    def state(self, root, rev):
        """kept | reverted | missing."""
        key = (root, rev)
        if key not in self._state:
            rc, full = self._run(root, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
            if rc != 0:
                s, full = "missing", rev
            else:
                full = full.strip()
                _, refs = self._run(root, "for-each-ref", "--contains", full, "--format=%(refname)",
                                    "refs/heads", "refs/remotes")
                if not refs.strip():
                    s = "missing"
                else:
                    _, rev_ = self._run(root, "log", "--all", "--format=%H", "--grep", "This reverts commit " + full)
                    s = "reverted" if rev_.strip() else "kept"
            self._state[key] = (s, full)
        return self._state[key]


def label_session(git, cwd, commits, commit_times, fixes, holdout):
    """One of: no_repo, holdout, eval_fix, success, failed, no_commit."""
    root = repo_root(cwd) if cwd and os.path.isdir(cwd) else None
    if not root:
        return "no_repo"
    if git.names(root) & holdout:
        return "holdout"
    full = set(git.near(root, commit_times)) | {git.state(root, c)[1] for c in set(commits)}
    if any(f.startswith(h.lower()) for h in full for f in fixes if len(h) >= 7):
        return "eval_fix"
    states = [git.state(root, h)[0] for h in full]
    if "kept" in states:
        return "success"
    return "failed" if states else "no_commit"
