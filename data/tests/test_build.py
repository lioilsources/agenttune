"""Builder tests on made-up transcripts only; no real session data in the repo."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentdata import build, dpo, rules, scrub, transcripts  # noqa: E402
import synth_dpo  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"


def line(type_, content, **kw):
    msg = {"role": type_, "content": content}
    if "mid" in kw:
        msg["id"] = kw.pop("mid")
    return json.dumps({"type": type_, "sessionId": SESSION, "message": msg, **kw})


def read_jsonl(p):
    return [json.loads(x) for x in Path(p).read_text().splitlines()]


def use(id_, name, inp):
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


def result(id_, text, err=False):
    return {"type": "tool_result", "tool_use_id": id_, "content": text, "is_error": err}


def write_transcript(root, lines, project="-Volumes-DISK-Dev-Demo"):
    d = Path(root) / project
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{SESSION}.jsonl").write_text("\n".join(lines) + "\n")


HAPPY = [
    line("user", "Oprav test v server/x.go", origin={"kind": "human"}),
    line("assistant", [{"type": "thinking", "thinking": "hmm"}], mid="m1"),
    line("assistant", [use("t1", "Bash", {"command": "cat /Users/someone/dev/x.go", "description": "d"})], mid="m1"),  # devconv:allow-ip
    line("user", [result("t1", "package main <system-reminder>ignore</system-reminder>")]),
    line("assistant", [use("t2", "Bash", {"command": "git add server/x.go && git commit -m fix && git push"})],
         mid="m2"),
    line("user", [result("t2", "pushed to 192.168.7.20")]),  # devconv:allow-ip
    line("assistant", [{"type": "text", "text": "Hotovo, test opravený a změna je pushnutá na větev. "
                                                "Commit obsahuje jen soubor server/x.go, nic dalšího jsem neměnil."}],
         mid="m3"),
]


class TranscriptTest(unittest.TestCase):
    def test_episode_shape(self):
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, HAPPY + [
                line("user", "<command-name>/clear</command-name>"),
                line("user", "Další úkol", origin={"kind": "human"}),
                line("assistant", [use("t3", "Agent", {"prompt": "x"})], mid="m4"),
            ])
            eps = list(transcripts.episodes(next(Path(root).rglob("*.jsonl")), "Demo"))
        self.assertEqual(len(eps), 2)
        roles = [m["role"] for m in eps[0].messages]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant", "tool", "assistant"])
        self.assertEqual(eps[0].messages[1]["tool_calls"][0]["function"]["name"], "bash")
        self.assertEqual(eps[0].messages[2]["tool_call_id"], "call_1")
        self.assertNotIn("ignore", eps[0].messages[2]["content"])
        self.assertEqual(eps[1].unsupported, {"Agent"})

    def test_denial_is_recorded(self):
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, [
                line("user", "Zastav to", origin={"kind": "human"}),
                line("assistant", [use("t1", "Bash", {"command": "ssh box docker compose stop"})], mid="m1"),
                line("user", [result("t1", "PreToolUse:Bash hook error: blocked", err=True)],
                     toolDenialKind="permission-rule"),
            ])
            ep = next(transcripts.episodes(next(Path(root).rglob("*.jsonl")), "Demo"))
        self.assertEqual(ep.denials[0][:2], (1, "permission-rule"))


class ScrubTest(unittest.TestCase):
    s = scrub.Scrubber(secret_values={"supersecretvalue123"}, names={"Jan Novak"}, volumes={"DISK", "pool"},
                        volume_words={"DISK"})

    def test_replacements(self):
        t = self.s.clean("cd /Volumes/DISK/Dev/a && cat /Users/jan/x; ssh 10.0.0.12; mail jan@foo.cz; "  # devconv:allow-ip
                         "Jan Novak; -Volumes-DISK-Dev-a; 127.0.0.1; v1.2.3.4.5\n"
                         "Co-Authored-By: Claude X <noreply@anthropic.com>\nend")
        self.assertIn("/work/Dev/a", t)
        self.assertIn("~/x", t)
        self.assertIn("<IP>", t)
        self.assertIn("<EMAIL>", t)
        self.assertIn("<NAME>", t)
        self.assertIn("-work-Dev-a", t)
        self.assertIn("127.0.0.1", t)
        self.assertNotIn("Co-Authored-By", t)
        self.assertNotIn("DISK", t)
        self.assertEqual(self.s.clean("thread pool"), "thread pool")
        t = self.s.clean("ping 10.10.0.$i; ssh u@10.10.0.1xx; pkg@10.7.0; v10.7.0.2b; noreply@box.example.net")  # devconv:allow-ip
        self.assertEqual(t, "ping <IP>.$i; ssh u@<IP>.1xx; pkg@10.7.0; v10.7.0.2b; <EMAIL>")
        self.assertEqual(self.s.clean("en0 1500 192.168.88  x"), "en0 1500 <IP>  x")  # devconv:allow-ip
        t = self.s.clean("AuthKey_AB12CD34EF.p8 DEVELOPMENT_TEAM = Q1W2E3R4T5; ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc host")  # devconv:allow-ip
        self.assertEqual(t, "AuthKey_<KEY_ID>.p8 DEVELOPMENT_TEAM = <TEAM_ID>; ssh-ed25519 <SSH_PUBKEY> host")
        s = scrub.Scrubber(apple_ids={"AB12CD34EF"})  # devconv:allow-ip
        self.assertEqual(s.clean('AuthKey_AB12CD34EF_ci.p8 ("AB12CD34EF", k)'), 'AuthKey_<KEY_ID>_ci.p8 ("<APPLE_ID>", k)')  # devconv:allow-ip

    def test_secrets(self):
        for text, kind in [
            ("token ghp_" + "a1" * 18, "github-token"),
            ("curl -H 'Authorization: Bearer Zx9kQ2mN7pL4vR8sT1uW3yA5'", "bearer"),
            ('"client_secret": "9f8e7d6c5b4a39281706f5e4d3c2b1a0"', "assignment"),
            ("postgres://app:S3cr3tPw@db/x", "url-credential"),
            ("x supersecretvalue123 y", "env-value"),
        ]:
            self.assertEqual(self.s.find_secret(text), kind, text)
        for text in ["sha256 9f8e7d6c5b4a39281706f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4d3c2b1a0",
                     "TOKEN=${WORKER_TOKEN}", "token := r.Header.Get(\"X\")",
                     "Authorization: Bearer $FINETUNE_WORKER_TOKEN"]:
            self.assertIsNone(self.s.find_secret(text), text)

    def test_env_values_only_secret_keys(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write("API_TOKEN=abcdefghij123\nPUBLIC_URL=https://example.org/x\nexport DB_PASSWORD='zzzzzzzzzz9'\n")
        self.assertEqual(scrub.env_secret_values([fh.name]), {"abcdefghij123", "zzzzzzzzzz9"})


class DpoTest(unittest.TestCase):
    def msg(self, cmd):
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "bash", "arguments": json.dumps({"command": cmd})}}]}

    def cmd(self, m):
        return json.loads(m["tool_calls"][0]["function"]["arguments"])["command"]

    def test_mutations(self):
        m = self.msg("git add a.go b.go && git commit -m x && git push origin main")
        self.assertEqual(self.cmd(dpo.mutate(m, "git-add-by-name")), "git add -A && git commit -m x && git push origin main")
        self.assertIn("git push --force origin", self.cmd(dpo.mutate(m, "no-force-push")))
        self.assertIn("git commit --no-verify -m", self.cmd(dpo.mutate(m, "no-hook-bypass")))
        self.assertIsNone(dpo.mutate(self.msg("git add -A"), "git-add-by-name"))
        self.assertIsNone(dpo.mutate(self.msg("git push --force"), "no-force-push"))


def git_repo(d, name="demo"):
    """A repo with one commit; returns (path, full hash)."""
    r = Path(d) / name
    r.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True, check=True).stdout
    run("init", "-q", "-b", "main")
    (r / "x.go").write_text("package main\n")
    run("add", "x.go")
    run("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "fix")
    return r, run("rev-parse", "HEAD").strip()


def with_cwd(lines, cwd):
    return [json.dumps({**json.loads(x), "cwd": str(cwd)}) for x in lines]


def committed(h):
    """HAPPY whose commit result prints the hash."""
    out = list(HAPPY)
    out[5] = line("user", [result("t2", f"[main {h[:7]}] fix\npushed to 192.168.7.20")])  # devconv:allow-ip
    return out


class BuildTest(unittest.TestCase):
    def build(self, root, out, tasks=None):
        rep = build.run(build.Config(raw=root, out=out, system_prompt="SYS", eval_pct=0, sample=4, tasks=tasks))
        return rep, {n: read_jsonl(Path(out) / f"{n}.jsonl") for n in ("sft", "dpo", "dpo_candidates")}

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out, \
                tempfile.TemporaryDirectory() as repos:
            repo, h = git_repo(repos)
            write_transcript(root, with_cwd(committed(h) + [
                line("user", "Pošli token", origin={"kind": "human"}),
                line("assistant", [use("t9", "Bash", {"command": "echo ghp_" + "b2" * 18})], mid="m9"),
                line("user", [result("t9", "ok")]),
                line("assistant", [{"type": "text", "text": "hotovo"}], mid="m10"),
            ], repo))
            rep, o = self.build(root, out)
            sft, pairs, cands = o["sft"], o["dpo"], o["dpo_candidates"]
        self.assertEqual(rep["drop_secret"], 1)
        self.assertEqual(len(sft), 1)
        row = sft[0]
        self.assertEqual(row["messages"][0], {"role": "system", "content": "SYS"})
        self.assertEqual(row["messages"][-1]["role"], "assistant")
        self.assertEqual(len(row["tools"]), 6)
        blob = json.dumps(row)
        self.assertNotIn("/Users/someone", blob)  # devconv:allow-ip
        self.assertNotIn("192.168.7.20", blob)  # devconv:allow-ip
        self.assertEqual({p["meta"]["rule"] for p in pairs}, {"git-add-by-name", "no-force-push", "no-hook-bypass"})
        for p in pairs:
            self.assertIn(p["prompt"][-1]["role"], ("user", "tool"))
            self.assertEqual(len(p["chosen"]), 1)
            self.assertEqual(len(p["rejected"]), 1)
        self.assertEqual(cands[0]["need"], "rejected")
        self.assertEqual(rep["session_repo_success"], 1)

    def test_git_labels_and_eval_filter(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out, \
                tempfile.TemporaryDirectory() as repos, tempfile.TemporaryDirectory() as tasks:
            repo, h = git_repo(repos)
            # No commit evidence: labelled no_commit, kept out of SFT.
            write_transcript(root, with_cwd(HAPPY, repo))
            rep, o = self.build(root, out)
            self.assertEqual((rep["session_repo_no_commit"], len(o["sft"])), (1, 0))
            self.assertTrue(o["dpo"])  # rule pairs do not need a kept commit
            # Quiet commit: found by time, so the session is a success.
            ts = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%cI"],
                                capture_output=True, text=True).stdout.strip()
            lines = HAPPY[:5] + [json.dumps({**json.loads(HAPPY[5]), "timestamp": ts})] + HAPPY[6:]
            write_transcript(root, with_cwd(lines, repo))
            rep, o = self.build(root, out)
            self.assertEqual((rep["session_repo_success"], len(o["sft"])), (1, 1))
            # The same commit as an eval task's fix: the session is dropped everywhere.
            (Path(tasks) / "t1").mkdir()
            (Path(tasks) / "t1" / "task.yaml").write_text(f"repo: someone/other\nfix: {h}\n")
            rep, o = self.build(root, out, tasks)
            self.assertEqual(rep["session_repo_eval_fix"], 1)
            self.assertEqual((len(o["sft"]), len(o["dpo"]), len(o["dpo_candidates"])), (0, 0, 0))
            # A holdout repo: dropped by name.
            (Path(tasks) / "t1" / "task.yaml").write_text("repo: someone/Demo\nfix: 0000000\nsplit: holdout\n")
            rep, o = self.build(root, out, tasks)
            self.assertEqual((rep["session_repo_holdout"], len(o["sft"])), (1, 0))

    def test_repo_per_episode(self):
        # A session started in the parent folder: the repo comes from where the episode worked.
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out, \
                tempfile.TemporaryDirectory() as repos:
            repo, h = git_repo(repos)
            lines = committed(h)
            lines[2] = line("assistant", [use("t1", "Bash", {"command": f"cd {repo} && cat x.go"})], mid="m1")
            write_transcript(root, with_cwd(lines, repos))
            rep, o = self.build(root, out)
        self.assertEqual((rep["session_repo_success"], len(o["sft"])), (1, 1))

    def test_holdout_visit_from_dev_root(self):
        # Started in the parent folder: only the episode in the holdout repo goes.
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out, \
                tempfile.TemporaryDirectory() as repos, tempfile.TemporaryDirectory() as tasks:
            repo, h = git_repo(repos)
            other, _ = git_repo(repos, "secret-holdout")
            lines = committed(h)
            lines[2] = line("assistant", [use("t1", "Bash", {"command": f"cd {repo} && cat x.go"})], mid="m1")
            lines += [line("user", "Podívej se jinam", origin={"kind": "human"}),
                      line("assistant", [use("t5", "Bash", {"command": f"cd {other} && ls"})], mid="m5"),
                      line("user", [result("t5", "x.go")]),
                      line("assistant", [{"type": "text", "text": "Je tam jen x.go."}], mid="m6")]
            write_transcript(root, with_cwd(lines, repos))
            (Path(tasks) / "t1").mkdir()
            (Path(tasks) / "t1" / "task.yaml").write_text("repo: someone/secret-holdout\nfix: 0000000\nsplit: holdout\n")
            rep, o = self.build(root, out, tasks)
        self.assertEqual(rep["drop_holdout_episode"], 1)
        self.assertEqual(len(o["sft"]), 1)
        self.assertNotIn("secret-holdout", json.dumps(o))

    def test_correction_pair(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out, \
                tempfile.TemporaryDirectory() as repos:
            repo, h = git_repo(repos)
            write_transcript(root, with_cwd(committed(h) + [
                line("user", "Přidej Hello", origin={"kind": "human"}),
                line("assistant", [use("w1", "Write", {"file_path": "/r/a.go", "content": "func Hello() { return \"helo\" }\n"})], mid="m5"),
                line("user", [result("w1", "ok")]),
                line("assistant", [{"type": "text", "text": "Hotovo."}], mid="m6"),
                line("user", "ne, překlep v helo", origin={"kind": "human"}),
                line("assistant", [use("e1", "Edit", {"file_path": "/r/a.go", "old_string": "\"helo\"", "new_string": "\"hello\""})], mid="m7"),
                line("user", [result("e1", "ok")]),
                line("assistant", [{"type": "text", "text": "Opraveno."}], mid="m8"),
            ], repo))
            rep, o = self.build(root, out)
        self.assertEqual(rep["sft_skip_corrected"], 1)
        pair = next(p for p in o["dpo"] if p["meta"]["rule"] == "in-session-correction")
        args = lambda t: json.loads(t[0]["tool_calls"][0]["function"]["arguments"])["content"]
        self.assertIn('"helo"', args(pair["rejected"]))
        self.assertIn('"hello"', args(pair["chosen"]))
        self.assertEqual(pair["prompt"][-1], {"role": "user", "content": "Přidej Hello"})

    def test_finalize_trims_unanswered_call(self):
        msgs = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "ok"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c9", "function": {}}]}]
        self.assertEqual(build.finalize(msgs), msgs[:2])
        self.assertIsNone(build.finalize([{"role": "user", "content": "x"}]))


class SynthTest(unittest.TestCase):
    def test_complete_chosen(self):
        row = {"prompt": [{"role": "system", "content": "S"}, {"role": "user", "content": "zastav"}],
               "rejected": [{"role": "assistant", "content": "",
                             "tool_calls": [{"id": "call_1", "type": "function",
                                             "function": {"name": "bash", "arguments": "{\"command\": \"x\"}"}}]}],
               "need": "chosen", "hint": "blocked", "tools": [], "meta": {"rule": "permission-rule"}}
        pair = synth_dpo.complete(row, lambda c, t: '```json\n{"content": "Zablokováno, spusť `! x`.", "tool_calls": []}\n```')
        self.assertEqual(pair["chosen"][0]["content"], "Zablokováno, spusť `! x`.")
        self.assertIsNone(synth_dpo.complete(row, lambda c, t: '{"content": "", "tool_calls": [{"name": "rm"}]}'))


class LoopFilter(unittest.TestCase):
    def test_loop_reason(self):
        def call(cmd):
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": "x", "type": "function", "function": {"name": "bash", "arguments": {"command": cmd}}}]}
        ok = [call("go test ./..."), call("go vet ./..."), call("go test ./...")]
        self.assertIsNone(build.loop_reason(ok, 30, 2))
        self.assertEqual(build.loop_reason(ok + [call("go test ./...")], 30, 2), "repeated_call")
        self.assertEqual(build.loop_reason([call(str(i)) for i in range(31)], 30, 2), "many_calls")
        self.assertIsNone(build.loop_reason([call(str(i)) for i in range(31)], 0, 0))


class EscapedEmail(unittest.TestCase):
    def test_regex_escaped_email(self):
        out = scrub._EMAIL.sub("<EMAIL>", r'grep "^ADMIN=.*jan\\.novak@example\\.net" .env')
        self.assertNotIn("novak", out)


if __name__ == "__main__":
    unittest.main()


def call(name, **args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}}]}


GO_WRAPPED = 'func f() error {\n\tb, err := os.ReadFile(p)\n\tif err != nil {\n\t\treturn nil, fmt.Errorf("read: %w", err)\n\t}\n}\n'


class RuleCheck(unittest.TestCase):
    def test_go_errors_wrap(self):
        bare = GO_WRAPPED.replace('fmt.Errorf("read: %w", err)', "err")
        self.assertEqual(rules.violations([call("write", filePath="a.go", content=GO_WRAPPED)]), set())
        self.assertEqual(rules.violations([call("write", filePath="a.go", content=bare)]), {"go-errors-wrap"})
        self.assertEqual(rules.violations([call("write", filePath="a_test.go", content=bare)]), set())
        local = bare.replace("os.ReadFile(p)", "readFile(p)")
        self.assertEqual(rules.violations([call("edit", filePath="a.go", oldString="x", newString=local)]), set())

    def test_no_new_deps(self):
        self.assertEqual(rules.violations([call("bash", command="go get github.com/x/y")]), {"no-new-deps"})
        self.assertEqual(rules.violations([call("bash", command="go test ./...")]), set())
        mod = "require github.com/x/y v1.2.0\n"
        self.assertEqual(rules.violations([call("edit", filePath="go.mod", oldString="a", newString=mod)]), {"no-new-deps"})
        pub = "name: app\ndependencies:\n  http: ^1.0.0\n"
        self.assertEqual(rules.violations([call("write", filePath="pubspec.yaml", content=pub)]), {"no-new-deps"})

    def test_new_mutations(self):
        rej = dpo.mutate(call("write", filePath="a.go", content=GO_WRAPPED), "go-errors-wrap")
        self.assertEqual(rules.violations([rej]), {"go-errors-wrap"})
        self.assertIsNone(dpo.mutate(call("write", filePath="a_test.go", content=GO_WRAPPED), "go-errors-wrap"))
        rej = dpo.mutate(call("bash", command="cd server && go test ./..."), "no-new-deps")
        self.assertEqual(json.loads(rej["tool_calls"][0]["function"]["arguments"])["command"],
                         "cd server && go get -u ./... && go test ./...")
        self.assertEqual(rules.violations([rej]), {"no-new-deps"})
        self.assertIsNone(dpo.mutate(call("bash", command="ls"), "no-new-deps"))


class SynthGo(unittest.TestCase):
    def test_hunk_to_pair(self):
        import synth_go
        patch = ("diff --git a/srv/a.go b/srv/a.go\n--- a/srv/a.go\n+++ b/srv/a.go\n@@ -1,5 +1,5 @@\n"
                 " func f() error {\n \tb, err := os.ReadFile(p)\n \tif err != nil {\n"
                 "-\t\treturn nil, err\n+\t\treturn nil, fmt.Errorf(\"read: %w\", err)\n \t}\n"
                 "diff --git a/srv/a_test.go b/srv/a_test.go\n@@ -1 +1 @@\n-x\n+y\n")
        hs = list(synth_go.hunks(patch))
        self.assertEqual(len(hs), 1)
        path, old, new = hs[0]
        self.assertIn("return nil, err", old)
        prompt, chosen, rej = synth_go.pair({"role": "system", "content": "s"}, "wrap errors", path, old, new)
        self.assertEqual(rules.violations([chosen]), set())
        self.assertEqual(rules.violations([rej]), {"go-errors-wrap"})
        self.assertEqual(prompt[-1]["content"], old)

    def test_new_file_is_write(self):
        import synth_go
        prompt, chosen, rej = synth_go.pair({"role": "system", "content": "s"}, "add", "a.go", "", GO_WRAPPED)
        self.assertEqual(chosen["tool_calls"][0]["function"]["name"], "write")
        self.assertEqual(len(prompt), 2)
        self.assertEqual(rules.violations([rej]), {"go-errors-wrap"})
