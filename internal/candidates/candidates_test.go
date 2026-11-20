package candidates

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func sh(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command(args[0], args[1:]...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_AUTHOR_DATE=2026-01-02T00:00:00Z", "GIT_COMMITTER_DATE=2026-01-02T00:00:00Z",
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("%v: %v\n%s", args, err, out)
	}
}

func put(t *testing.T, dir, name, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestFind(t *testing.T) {
	dir := t.TempDir()
	sh(t, dir, "git", "init", "-q", "-b", "main")
	put(t, dir, "q.go", "package q\n")
	sh(t, dir, "git", "add", "q.go")
	sh(t, dir, "git", "commit", "-q", "-m", "init")
	put(t, dir, "q.go", "package q\n\nfunc Retry() int { return 3 }\n")
	put(t, dir, "q_test.go", "package q\n\nimport \"testing\"\n\nfunc TestRetry(t *testing.T) {\n\tif Retry() != 3 {\n\t\tt.Fatal(\"x\")\n\t}\n}\n")
	sh(t, dir, "git", "add", "q.go", "q_test.go")
	sh(t, dir, "git", "commit", "-q", "-m", "queue: retry failed jobs")
	put(t, dir, "README.md", "x\n")
	sh(t, dir, "git", "add", "README.md")
	sh(t, dir, "git", "commit", "-q", "-m", "docs")

	got, err := Find(context.Background(), dir, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d candidates: %+v", len(got), got)
	}
	c := got[0]
	if c.Subject != "queue: retry failed jobs" || c.Date != "2026-01-02" || len(c.Tests) != 1 || c.Tests[0] != "TestRetry" || c.Parent == "" {
		t.Fatalf("candidate = %+v", c)
	}
}
