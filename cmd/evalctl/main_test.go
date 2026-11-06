package main

import (
	"bytes"
	"context"
	"strings"
	"testing"

	"github.com/lioilsources/agenttune/internal/candidates"
)

func TestRulesCheckStdin(t *testing.T) {
	d := "diff --git a/q.go b/q.go\n--- a/q.go\n+++ b/q.go\n@@ -1,1 +1,2 @@\n package q\n+func f() error { return err }\n"
	var out bytes.Buffer
	code, err := dispatch(context.Background(), []string{"rules", "check", "-rules", "tests-added", "-"}, strings.NewReader(d), &out)
	if err != nil || code != 1 || !strings.Contains(out.String(), `"rule":"tests-added"`) {
		t.Fatalf("code %d err %v out %s", code, err, out.String())
	}
	out.Reset()
	if code, err := dispatch(context.Background(), []string{"rules", "check", "-rules", "nope", "-"}, strings.NewReader(d), &out); err == nil || code != 2 {
		t.Fatalf("unknown rule: code %d err %v", code, err)
	}
}

func TestDraft(t *testing.T) {
	c := candidates.Commit{SHA: "bbbbbbb", Parent: "aaaaaaa", TestFiles: []string{"internal/queue/q_test.go", "q_test.go"}, Tests: []string{"TestRetry", "TestBackoff"}}
	tk, err := draft(c, "lioilsources/AiStack", "aistack-retry-001", "holdout", "queue: retry", []string{"go.mod"})
	if err != nil {
		t.Fatal(err)
	}
	if err := tk.Validate(); err != nil {
		t.Fatal(err)
	}
	if tk.Commit != "aaaaaaa" || tk.Fix != "bbbbbbb" || tk.Lang != "go" {
		t.Fatalf("task = %+v", tk)
	}
	if want := "go test . ./internal/queue -run '^(TestRetry|TestBackoff)$'"; tk.TestCmd != want {
		t.Fatalf("test_cmd = %q, want %q", tk.TestCmd, want)
	}
	sub, err := draft(candidates.Commit{SHA: "bbbbbbb", Parent: "aaaaaaa", TestFiles: []string{"services/gq/internal/p/p_test.go"}, Tests: []string{"TestP"}},
		"lioilsources/AiStack", "aistack-p-001", "dev", "x", []string{"gateway/go.mod", "services/gq/go.mod"})
	if err != nil || sub.Dir != "services/gq" || sub.TestCmd != "go test ./internal/p -run '^(TestP)$'" {
		t.Fatalf("submodule task = %+v, err %v", sub, err)
	}
	if _, err := draft(candidates.Commit{SHA: "bbbbbbb", Parent: "aaaaaaa", TestFiles: []string{"a/x_test.go", "b/y_test.go"}},
		"r/r", "r-x-001", "dev", "x", []string{"a/go.mod", "b/go.mod"}); err == nil {
		t.Fatal("tests in two modules should fail")
	}
	f, _ := draft(candidates.Commit{SHA: "bbbbbbb", Parent: "aaaaaaa", TestFiles: []string{"test/a_test.dart"}}, "lioilsources/SwypeKids", "swypekids-a-001", "dev", "x", []string{"pubspec.yaml"})
	if f.Lang != "flutter" || f.Dir != "" || f.TestCmd != "flutter test test/a_test.dart" {
		t.Fatalf("flutter task = %+v", f)
	}
	app, err := draft(candidates.Commit{SHA: "bbbbbbb", Parent: "aaaaaaa", TestFiles: []string{"app/test/a_test.dart", "app/test/b/b_test.dart"}},
		"r/r", "r-app-001", "dev", "x", []string{"pubspec.yaml", "app/pubspec.yaml", "app/packages/x/pubspec.yaml"})
	if err != nil || app.Dir != "app" || app.TestCmd != "flutter test test/a_test.dart test/b/b_test.dart" {
		t.Fatalf("flutter subdir task = %+v, err %v", app, err)
	}
	if _, err := draft(candidates.Commit{SHA: "bbbbbbb", Parent: "aaaaaaa", TestFiles: []string{"test/a_test.dart"}}, "r/r", "r-x-001", "dev", "x", []string{"go.mod"}); err == nil {
		t.Fatal("dart test outside a Flutter package should fail")
	}
}

func TestStripTrailers(t *testing.T) {
	got := stripTrailers("fix: x\n\nBody.\n\nCo-Authored-By: A <a@example.com>\nClaude-Session: https://example.com/s\n")
	if got != "fix: x\n\nBody." {
		t.Fatalf("got %q", got)
	}
}
