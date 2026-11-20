package task

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const good = `id: aistack-queue-retry-001
repo: lioilsources/AiStack
commit: 0123abc
lang: go
prompt: |
  Add retry.
test_cmd: go test ./internal/queue/... -run TestRetry
timeout_s: 600
rules: [go-errors-wrap, no-new-deps]
split: dev
`

func write(t *testing.T, dir, id, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(dir, id), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, id, "task.yaml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestLoadDir(t *testing.T) {
	dir := t.TempDir()
	write(t, dir, "a", good)
	ts, err := LoadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(ts) != 1 || ts[0].TimeoutS != 600 || ts[0].Split != Dev {
		t.Fatalf("got %+v", ts)
	}
	write(t, dir, "b", good)
	if _, err := LoadDir(dir); err == nil || !strings.Contains(err.Error(), "duplicate") {
		t.Fatalf("want duplicate error, got %v", err)
	}
}

func TestValidateReportsAll(t *testing.T) {
	dir := t.TempDir()
	write(t, dir, "x", "id: Bad\nrepo: r\ncommit: zz\nlang: rust\nprompt: p\ntest_cmd: c\ntimeout_s: 0\nrules: [nope]\nsplit: train\n")
	_, err := LoadDir(dir)
	if err == nil {
		t.Fatal("want error")
	}
	for _, want := range []string{"id", "sha", "lang", "timeout_s", "split", "unknown rules"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q lacks %q", err, want)
		}
	}
}

func TestUnknownField(t *testing.T) {
	dir := t.TempDir()
	write(t, dir, "x", good+"extra: 1\n")
	if _, err := LoadDir(dir); err == nil {
		t.Fatal("want unknown field error")
	}
}
