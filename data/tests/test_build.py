"""Builder tests on made-up transcripts only; no real session data in the repo."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentdata import build, dpo, scrub, transcripts  # noqa: E402
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


class BuildTest(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out:
            write_transcript(root, HAPPY + [
                line("user", "Pošli token", origin={"kind": "human"}),
                line("assistant", [use("t9", "Bash", {"command": "echo ghp_" + "b2" * 18})], mid="m9"),
                line("user", [result("t9", "ok")]),
                line("assistant", [{"type": "text", "text": "hotovo"}], mid="m10"),
            ])
            cfg = build.Config(raw=root, out=out, system_prompt="SYS", eval_pct=0, sample=4)
            rep = build.run(cfg)
            sft = read_jsonl(Path(out) / "sft.jsonl")
            pairs = read_jsonl(Path(out) / "dpo.jsonl")
            cands = read_jsonl(Path(out) / "dpo_candidates.jsonl")
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


if __name__ == "__main__":
    unittest.main()
