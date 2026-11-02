// Command evalctl builds and runs the agenttune eval: rule checks over diffs,
// candidate commits, task drafts and the task runner.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"os/signal"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/lioilsources/agenttune/internal/candidates"
	"github.com/lioilsources/agenttune/internal/diff"
	"github.com/lioilsources/agenttune/internal/rules"
	"github.com/lioilsources/agenttune/internal/runner"
	"github.com/lioilsources/agenttune/internal/task"
)

const usage = `evalctl — agenttune eval harness

  evalctl rules list
  evalctl rules check [-C dir] [-rules a,b] [-allow-deps] <rev>|-   diff of worktree vs rev, or unified diff on stdin
  evalctl candidates [-n N] <repo-dir>                               commits that change code and tests
  evalctl draft -repo owner/name -dir <clone> -sha <fix> -id <name-NNN> [-split dev|holdout] [-tasks evals/tasks] [-force]
  evalctl validate [-tasks evals/tasks]
  evalctl verify -repos-root DIR [-tasks evals/tasks] [-only id,id]          oracle fails before the fix and passes with it
  evalctl run -model M -agent-cmd '<template>' -repos-root DIR [-tasks evals/tasks] [-split dev|holdout|all] [-only id,id] [-out evals/results] [-keep]
`

func main() {
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, nil)))
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	code, err := dispatch(ctx, os.Args[1:], os.Stdin, os.Stdout)
	if err != nil {
		fmt.Fprintln(os.Stderr, "evalctl:", err)
		if code == 0 {
			code = 2
		}
	}
	os.Exit(code)
}

func dispatch(ctx context.Context, args []string, stdin io.Reader, stdout io.Writer) (int, error) {
	if len(args) == 0 {
		fmt.Fprint(stdout, usage)
		return 2, nil
	}
	switch args[0] {
	case "rules":
		if len(args) > 1 && args[1] == "list" {
			for _, r := range rules.All() {
				fmt.Fprintf(stdout, "%-28s %s\n", r.ID, r.Source)
			}
			return 0, nil
		}
		if len(args) > 1 && args[1] == "check" {
			return rulesCheck(ctx, args[2:], stdin, stdout)
		}
	case "candidates":
		return 0, cmdCandidates(ctx, args[1:], stdout)
	case "draft":
		return 0, cmdDraft(ctx, args[1:], stdout)
	case "validate":
		return 0, cmdValidate(args[1:], stdout)
	case "run":
		return cmdRun(ctx, args[1:], stdout)
	case "verify":
		return cmdVerify(ctx, args[1:], stdout)
	}
	fmt.Fprint(stdout, usage)
	return 2, nil
}

func splitList(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func rulesCheck(ctx context.Context, args []string, stdin io.Reader, stdout io.Writer) (int, error) {
	fs := flag.NewFlagSet("rules check", flag.ContinueOnError)
	dir := fs.String("C", ".", "repository directory")
	only := fs.String("rules", "", "comma-separated rule IDs (default: all)")
	allowDeps := fs.Bool("allow-deps", false, "the task explicitly asks for a dependency")
	if err := fs.Parse(args); err != nil {
		return 2, err
	}
	if fs.NArg() != 1 {
		return 2, errors.New("rules check: want <rev> or -")
	}
	var text []byte
	var err error
	if fs.Arg(0) == "-" {
		text, err = io.ReadAll(stdin)
	} else {
		text, err = exec.CommandContext(ctx, "git", "-C", *dir, "diff", "--no-color", "--no-ext-diff", fs.Arg(0)).Output()
	}
	if err != nil {
		return 2, fmt.Errorf("read diff: %w", err)
	}
	rs := rules.All()
	if *only != "" {
		var unk []string
		if rs, unk = rules.Select(splitList(*only)); len(unk) > 0 {
			return 2, fmt.Errorf("unknown rules %v", unk)
		}
	}
	vs := rules.Run(rs, rules.Input{
		Files:     diff.Parse(string(text)),
		AllowDeps: *allowDeps,
		Read: func(p string) []byte {
			b, _ := os.ReadFile(filepath.Join(*dir, filepath.FromSlash(p)))
			return b
		},
	})
	enc := json.NewEncoder(stdout)
	for _, v := range vs {
		if err := enc.Encode(v); err != nil {
			return 2, fmt.Errorf("write: %w", err)
		}
	}
	if len(vs) > 0 {
		return 1, nil
	}
	return 0, nil
}

func cmdCandidates(ctx context.Context, args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("candidates", flag.ContinueOnError)
	n := fs.Int("n", 0, "max commits (0 = all)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return errors.New("candidates: want <repo-dir>")
	}
	cs, err := candidates.Find(ctx, fs.Arg(0), *n)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(stdout)
	for _, c := range cs {
		if err := enc.Encode(c); err != nil {
			return fmt.Errorf("write: %w", err)
		}
	}
	return nil
}

var defaultRules = map[string][]string{
	"go":      {"go-errors-wrap", "no-fmt-println", "go-test-no-time-now", "no-edit-migrations", "no-edit-generated", "no-new-deps", "tests-added", "no-secrets-files"},
	"flutter": {"flutter-no-hardcoded-text", "flutter-no-logic-in-build", "no-edit-generated", "no-new-deps", "tests-added", "no-release-workflow-edit", "no-secrets-files"},
}

func cmdDraft(ctx context.Context, args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("draft", flag.ContinueOnError)
	repo := fs.String("repo", "", "owner/name")
	dir := fs.String("dir", "", "local clone")
	sha := fs.String("sha", "", "fix commit")
	id := fs.String("id", "", "task id <name>-NNN")
	split := fs.String("split", "dev", "dev or holdout")
	tasksDir := fs.String("tasks", "evals/tasks", "tasks directory")
	force := fs.Bool("force", false, "overwrite an existing task.yaml")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *repo == "" || *dir == "" || *sha == "" || *id == "" {
		return errors.New("draft: -repo, -dir, -sha and -id are required")
	}
	cs, err := candidates.Find(ctx, *dir, 0)
	if err != nil {
		return err
	}
	var c *candidates.Commit
	for i := range cs {
		if strings.HasPrefix(cs[i].SHA, *sha) {
			c = &cs[i]
			break
		}
	}
	if c == nil {
		return fmt.Errorf("draft: %s is not a code+test commit in %s", *sha, *dir)
	}
	body, err := exec.CommandContext(ctx, "git", "-C", *dir, "log", "-1", "--format=%B", c.SHA).Output()
	if err != nil {
		return fmt.Errorf("read commit message: %w", err)
	}
	tree, err := exec.CommandContext(ctx, "git", "-C", *dir, "ls-tree", "-r", "--name-only", c.SHA).Output()
	if err != nil {
		return fmt.Errorf("list files: %w", err)
	}
	var mods []string
	for _, f := range strings.Split(string(tree), "\n") {
		if path.Base(f) == "go.mod" {
			mods = append(mods, path.Dir(f))
		}
	}
	t, err := draft(*c, *repo, *id, task.Split(*split), stripTrailers(string(body)), mods)
	if err != nil {
		return err
	}
	if err := t.Validate(); err != nil {
		return fmt.Errorf("draft is invalid: %w", err)
	}
	b, err := task.Marshal(t)
	if err != nil {
		return err
	}
	out := filepath.Join(*tasksDir, t.ID, "task.yaml")
	if _, err := os.Stat(out); err == nil && !*force {
		return fmt.Errorf("draft: %s exists", out)
	}
	if err := os.MkdirAll(filepath.Dir(out), 0o755); err != nil {
		return fmt.Errorf("create task dir: %w", err)
	}
	if err := os.WriteFile(out, b, 0o644); err != nil {
		return fmt.Errorf("write task: %w", err)
	}
	fmt.Fprintln(stdout, out)
	return nil
}

// moduleOf returns the longest module dir (from mods, "." = root) that contains file.
func moduleOf(file string, mods []string) (string, bool) {
	best, found := "", false
	for _, m := range mods {
		if (m == "." || strings.HasPrefix(file, m+"/")) && (!found || len(m) > len(best)) {
			best, found = m, true
		}
	}
	return best, found
}

// draft derives a task from a candidate. The prompt is the commit message and must be
// rewritten by hand into a request that does not reveal the solution. mods lists the
// directories with a go.mod at the fix commit.
func draft(c candidates.Commit, repo, id string, split task.Split, msg string, mods []string) (task.Task, error) {
	lang := "go"
	for _, f := range c.TestFiles {
		if strings.HasSuffix(f, ".dart") {
			lang = "flutter"
		}
	}
	t := task.Task{
		ID: id, Repo: repo, Commit: c.Parent, Fix: c.SHA, Lang: lang, TimeoutS: 900, Split: split,
		TestFiles: c.TestFiles, Rules: defaultRules[lang],
		Prompt: "TODO: rewrite as a request without the solution.\n\n" + msg + "\n",
	}
	if lang == "go" {
		pkgs := map[string]bool{}
		mod := ""
		for i, f := range c.TestFiles {
			m, ok := moduleOf(f, mods)
			if !ok {
				return t, fmt.Errorf("draft: %s is in no Go module", f)
			}
			if i > 0 && m != mod {
				return t, fmt.Errorf("draft: tests span modules %s and %s; split the task", mod, m)
			}
			mod = m
			rel := strings.TrimPrefix(path.Dir(f), m)
			rel = strings.TrimPrefix(rel, "/")
			if m == "." {
				rel = path.Dir(f)
			}
			if rel == "." || rel == "" {
				pkgs["."] = true
			} else {
				pkgs["./"+rel] = true
			}
		}
		if mod != "." {
			t.Dir = mod
		}
		var ps []string
		for p := range pkgs {
			ps = append(ps, p)
		}
		sort.Strings(ps)
		t.TestCmd = "go test " + strings.Join(ps, " ")
		if len(c.Tests) > 0 {
			t.TestCmd += " -run '^(" + strings.Join(c.Tests, "|") + ")$'"
		}
		return t, nil
	}
	t.TestCmd = "flutter test " + strings.Join(c.TestFiles, " ")
	return t, nil
}

func cmdValidate(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("validate", flag.ContinueOnError)
	dir := fs.String("tasks", "evals/tasks", "tasks directory")
	if err := fs.Parse(args); err != nil {
		return err
	}
	ts, err := task.LoadDir(*dir)
	if err != nil {
		return err
	}
	n := map[task.Split]int{}
	todo := 0
	for _, t := range ts {
		n[t.Split]++
		if strings.HasPrefix(t.Prompt, "TODO") {
			todo++
		}
	}
	fmt.Fprintf(stdout, "%d tasks: %d dev, %d holdout, %d prompts still TODO\n", len(ts), n[task.Dev], n[task.Holdout], todo)
	return nil
}

func cmdRun(ctx context.Context, args []string, stdout io.Writer) (int, error) {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	model := fs.String("model", "", "model name passed to the agent")
	agentCmd := fs.String("agent-cmd", "", "agent shell template ({workdir} {prompt_file} {model} {metrics_file})")
	reposRoot := fs.String("repos-root", "", "directory with local clones named like the repo")
	dir := fs.String("tasks", "evals/tasks", "tasks directory")
	split := fs.String("split", "dev", "dev, holdout or all")
	only := fs.String("only", "", "comma-separated task IDs")
	outDir := fs.String("out", "evals/results", "results directory")
	keep := fs.Bool("keep", false, "keep clones")
	if err := fs.Parse(args); err != nil {
		return 2, err
	}
	if *model == "" || *agentCmd == "" || *reposRoot == "" {
		return 2, errors.New("run: -model, -agent-cmd and -repos-root are required")
	}
	ts, err := task.LoadDir(*dir)
	if err != nil {
		return 2, err
	}
	want := map[string]bool{}
	for _, id := range splitList(*only) {
		want[id] = true
	}
	var sel []task.Task
	for _, t := range ts {
		if (*split == "all" || string(t.Split) == *split) && (len(want) == 0 || want[t.ID]) {
			sel = append(sel, t)
		}
	}
	if len(sel) == 0 {
		return 2, errors.New("run: no tasks selected")
	}
	work, err := os.MkdirTemp("", "agenttune-run-")
	if err != nil {
		return 2, fmt.Errorf("create work root: %w", err)
	}
	if !*keep {
		defer os.RemoveAll(work)
	}
	cfg := runner.Config{
		Model: *model, AgentCmd: *agentCmd, WorkRoot: work, Keep: *keep, Clock: time.Now,
		RepoDir: repoDirFunc(*reposRoot),
	}
	stamp := time.Now().Format("2006-01-02T1504")
	base := filepath.Join(*outDir, stamp+"-"+sanitize(*model))
	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		return 2, fmt.Errorf("create results dir: %w", err)
	}
	jf, err := os.Create(base + ".jsonl")
	if err != nil {
		return 2, fmt.Errorf("create results: %w", err)
	}
	defer jf.Close()
	jw := bufio.NewWriter(jf)
	enc := json.NewEncoder(jw)
	var rs []runner.Result
	for i, t := range sel {
		if ctx.Err() != nil {
			break
		}
		slog.Info("task", "n", i+1, "of", len(sel), "id", t.ID)
		r := runner.Run(ctx, cfg, t)
		rs = append(rs, r)
		if err := enc.Encode(r); err != nil {
			return 2, fmt.Errorf("write result: %w", err)
		}
		if err := os.WriteFile(filepath.Join(*outDir, stamp+"-"+sanitize(*model)+"-"+t.ID+".diff"), []byte(r.Diff), 0o644); err != nil {
			return 2, fmt.Errorf("write diff: %w", err)
		}
	}
	if err := jw.Flush(); err != nil {
		return 2, fmt.Errorf("flush results: %w", err)
	}
	mf, err := os.Create(base + ".md")
	if err != nil {
		return 2, fmt.Errorf("create report: %w", err)
	}
	defer mf.Close()
	if err := runner.WriteMarkdown(io.MultiWriter(mf, stdout), *model, rs); err != nil {
		return 2, fmt.Errorf("write report: %w", err)
	}
	return 0, nil
}

func sanitize(s string) string {
	return strings.Map(func(r rune) rune {
		if r == '/' || r == ':' || r == ' ' {
			return '_'
		}
		return r
	}, s)
}

func repoDirFunc(root string) func(string) (string, error) {
	return func(repo string) (string, error) {
		p := filepath.Join(root, path.Base(repo))
		if _, err := os.Stat(filepath.Join(p, ".git")); err != nil {
			return "", fmt.Errorf("no clone of %s at %s: %w", repo, p, err)
		}
		return p, nil
	}
}

// cmdVerify runs each task twice: a no-op agent (oracle must fail) and an agent that
// applies the original fix (oracle must pass). Exit 1 if any task is not usable.
func cmdVerify(ctx context.Context, args []string, stdout io.Writer) (int, error) {
	fs := flag.NewFlagSet("verify", flag.ContinueOnError)
	reposRoot := fs.String("repos-root", "", "directory with local clones named like the repo")
	dir := fs.String("tasks", "evals/tasks", "tasks directory")
	only := fs.String("only", "", "comma-separated task IDs")
	if err := fs.Parse(args); err != nil {
		return 2, err
	}
	if *reposRoot == "" {
		return 2, errors.New("verify: -repos-root is required")
	}
	ts, err := task.LoadDir(*dir)
	if err != nil {
		return 2, err
	}
	want := map[string]bool{}
	for _, id := range splitList(*only) {
		want[id] = true
	}
	work, err := os.MkdirTemp("", "agenttune-verify-")
	if err != nil {
		return 2, fmt.Errorf("create work root: %w", err)
	}
	defer os.RemoveAll(work)
	cfg := runner.Config{Model: "verify", WorkRoot: work, Clock: time.Now, RepoDir: repoDirFunc(*reposRoot)}
	bad := 0
	fmt.Fprintln(stdout, "| task | before fix | with fix | usable | note |\n|---|---|---|---|---|")
	for _, t := range ts {
		if (len(want) > 0 && !want[t.ID]) || ctx.Err() != nil {
			continue
		}
		if t.Fix == "" || len(t.TestFiles) == 0 {
			bad++
			fmt.Fprintf(stdout, "| %s | | | no | no fix or test_files |\n", t.ID)
			continue
		}
		cfg.AgentCmd = "true"
		before := runner.Run(ctx, cfg, t)
		cfg.AgentCmd = "git checkout {fix} -- ."
		with := runner.Run(ctx, cfg, t)
		ok := before.Error == "" && with.Error == "" && !before.Pass && with.Pass
		note := strings.TrimSpace(before.Error + " " + with.Error)
		if !ok {
			bad++
		}
		fmt.Fprintf(stdout, "| %s | %s | %s | %s | %s |\n", t.ID, outcome(before), outcome(with), map[bool]string{true: "yes", false: "no"}[ok], note)
	}
	if bad > 0 {
		return 1, nil
	}
	return 0, nil
}

func outcome(r runner.Result) string {
	if r.Error != "" {
		return "error"
	}
	if r.Pass {
		return "pass"
	}
	return fmt.Sprintf("fail (%d)", r.TestExit)
}

var trailer = regexp.MustCompile(`(?i)^(Co-Authored-By|Claude-Session|Signed-off-by|Reviewed-by|Generated-by):`)

// stripTrailers drops git trailers (attribution, session links) from a commit message.
func stripTrailers(msg string) string {
	var keep []string
	for _, l := range strings.Split(msg, "\n") {
		if !trailer.MatchString(strings.TrimSpace(l)) {
			keep = append(keep, l)
		}
	}
	return strings.TrimSpace(strings.Join(keep, "\n"))
}
