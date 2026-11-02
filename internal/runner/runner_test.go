package runner

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/lioilsources/agenttune/internal/task"
)

func sh(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command(args[0], args[1:]...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("%v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func put(t *testing.T, dir, name, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// fixture: commit A has a stub, commit B (fix) implements it and adds the oracle test.
func fixture(t *testing.T) (repo, before, fix string) {
	repo = t.TempDir()
	sh(t, repo, "git", "init", "-q", "-b", "main")
	put(t, repo, "go.mod", "module q\n\ngo 1.22\n")
	put(t, repo, "q.go", "package q\n\nfunc Retries() int { return 0 }\n")
	sh(t, repo, "git", "add", "go.mod", "q.go")
	sh(t, repo, "git", "commit", "-q", "-m", "stub")
	before = sh(t, repo, "git", "rev-parse", "HEAD")
	put(t, repo, "q.go", "package q\n\nfunc Retries() int { return 3 }\n")
	put(t, repo, "q_test.go", "package q\n\nimport \"testing\"\n\nfunc TestRetries(t *testing.T) {\n\tif Retries() != 3 {\n\t\tt.Fatal(\"want 3\")\n\t}\n}\n")
	sh(t, repo, "git", "add", "q.go", "q_test.go")
	sh(t, repo, "git", "commit", "-q", "-m", "retry 3 times")
	fix = sh(t, repo, "git", "rev-parse", "HEAD")
	return repo, before, fix
}

func TestRun(t *testing.T) {
	if _, err := exec.LookPath("go"); err != nil {
		t.Skip("go not on PATH")
	}
	repo, before, fix := fixture(t)
	tk := task.Task{ID: "q-retry-001", Repo: "x/q", Commit: before, Fix: fix, Lang: "go", Prompt: "Retry 3 times.",
		TestCmd: "go test ./...", TestFiles: []string{"q_test.go"}, TimeoutS: 120, Split: task.Dev,
		Rules: []string{"tests-added", "no-fmt-println"}}
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	cfg := Config{
		Model:    "fake",
		RepoDir:  func(string) (string, error) { return repo, nil },
		WorkRoot: t.TempDir(),
		Clock:    func() time.Time { now = now.Add(5 * time.Second); return now },
	}

	// A good agent implements the fix and writes metrics.
	cfg.AgentCmd = `test -s {prompt_file} && printf 'package q\n\nfunc Retries() int { return 3 }\n' > q.go && echo '{"turns":4,"tokens":900}' > {metrics_file}`
	r := Run(context.Background(), cfg, tk)
	if r.Error != "" || !r.Pass || r.Turns != 4 || r.Tokens != 900 || r.WallTimeS != 5 {
		t.Fatalf("good agent: %+v", r)
	}
	if r.RuleViolations != 1 || r.Violations[0].Rule != "tests-added" {
		t.Fatalf("good agent without tests should violate tests-added: %+v", r.Violations)
	}

	// A bad agent adds a new file with fmt.Println and does not fix the code.
	cfg.AgentCmd = `printf 'package q\n\nimport "fmt"\n\nfunc Hi() { fmt.Println("x") }\n' > hi.go`
	r = Run(context.Background(), cfg, tk)
	if r.Error != "" || r.Pass || r.TestExit == 0 {
		t.Fatalf("bad agent should fail the oracle: %+v", r)
	}
	if !strings.Contains(r.Diff, "+++ b/hi.go") {
		t.Fatalf("untracked file missing from diff:\n%s", r.Diff)
	}
	var rules []string
	for _, v := range r.Violations {
		rules = append(rules, v.Rule)
	}
	if strings.Join(rules, ",") != "tests-added,no-fmt-println" {
		t.Fatalf("violations = %v", rules)
	}

	var md bytes.Buffer
	if err := WriteMarkdown(&md, "fake", []Result{r}); err != nil || !strings.Contains(md.String(), "| q-retry-001 | dev | false |") {
		t.Fatalf("markdown:\n%s (err %v)", md.String(), err)
	}
	if bytes.Contains([]byte(sh(t, repo, "git", "status", "--porcelain")), []byte("hi.go")) {
		t.Fatal("source repo was modified")
	}
}

func TestTimeout(t *testing.T) {
	repo, before, _ := fixture(t)
	tk := task.Task{ID: "q-slow-001", Repo: "x/q", Commit: before, Lang: "go", Prompt: "p", TestCmd: "true", TimeoutS: 1, Split: task.Holdout}
	cfg := Config{AgentCmd: "sleep 30", RepoDir: func(string) (string, error) { return repo, nil }, WorkRoot: t.TempDir(), Clock: time.Now}
	r := Run(context.Background(), cfg, tk)
	if r.AgentExit != 124 || !r.Pass {
		t.Fatalf("timeout: %+v", r)
	}
}

func TestSummarize(t *testing.T) {
	s := Summarize([]Result{
		{Split: task.Dev, Pass: true, RuleViolations: 2, Turns: 3},
		{Split: task.Dev, Pass: false, RuleViolations: 0, Turns: 5},
		{Split: task.Dev, Error: "clone failed"},
		{Split: task.Holdout, Pass: true, Turns: 7},
	})
	if len(s) != 2 {
		t.Fatalf("got %+v", s)
	}
	d := s[0]
	if d.Tasks != 3 || d.Errors != 1 || d.PassAt1 != 1.0/3 || d.MeanViolations != 1 || d.MedianTurns != 4 {
		t.Fatalf("dev = %+v", d)
	}
}
