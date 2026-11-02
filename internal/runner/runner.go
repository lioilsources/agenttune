// Package runner executes eval tasks: clean clone at the task commit, agent run,
// rule checks on the agent's diff, oracle tests from the fix commit, metrics.
package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/lioilsources/agenttune/internal/diff"
	"github.com/lioilsources/agenttune/internal/rules"
	"github.com/lioilsources/agenttune/internal/task"
)

// Config is shared by all tasks of one run.
type Config struct {
	Model string
	// AgentCmd is a shell template run in the clone. Placeholders: {workdir},
	// {prompt_file}, {model}, {metrics_file}, {fix} (only for task verification). The agent may write
	// {"turns":N,"tokens":N} to {metrics_file}.
	AgentCmd string
	// RepoDir maps "owner/name" to a local clone to copy from.
	RepoDir  func(repo string) (string, error)
	WorkRoot string // where clones go; each task gets its own subdirectory
	Keep     bool   // keep clones for inspection
	Clock    func() time.Time
}

// Result is the outcome of one task.
type Result struct {
	Task           string            `json:"task"`
	Split          task.Split        `json:"split"`
	Model          string            `json:"model"`
	Pass           bool              `json:"pass"`
	AgentExit      int               `json:"agent_exit"`
	TestExit       int               `json:"test_exit"`
	Violations     []rules.Violation `json:"violations"`
	RuleViolations int               `json:"rule_violations"`
	Turns          int               `json:"turns"`
	Tokens         int               `json:"tokens"`
	WallTimeS      float64           `json:"wall_time_s"`
	DiffSize       int               `json:"diff_size"` // added + removed lines
	Error          string            `json:"error,omitempty"`
	Diff           string            `json:"-"`
}

// Run executes one task. Infrastructure failures go to Result.Error; agent and test
// failures are ordinary results.
func Run(ctx context.Context, cfg Config, t task.Task) Result {
	r := Result{Task: t.ID, Split: t.Split, Model: cfg.Model, AgentExit: -1, TestExit: -1}
	if err := run(ctx, cfg, t, &r); err != nil {
		r.Error = err.Error()
		slog.Error("task failed", "task", t.ID, "err", err)
	}
	return r
}

func run(ctx context.Context, cfg Config, t task.Task, r *Result) error {
	src, err := cfg.RepoDir(t.Repo)
	if err != nil {
		return fmt.Errorf("locate repo: %w", err)
	}
	base := filepath.Join(cfg.WorkRoot, t.ID)
	if err := os.RemoveAll(base); err != nil {
		return fmt.Errorf("clean workdir: %w", err)
	}
	if err := os.MkdirAll(base, 0o755); err != nil {
		return fmt.Errorf("create workdir: %w", err)
	}
	if !cfg.Keep {
		defer os.RemoveAll(base)
	}
	wd := filepath.Join(base, "repo")
	if _, err := git(ctx, "", "clone", "-q", "--shared", "--no-checkout", src, wd); err != nil {
		return err
	}
	if _, err := git(ctx, wd, "checkout", "-q", "--detach", t.Commit); err != nil {
		return err
	}

	promptFile := filepath.Join(base, "prompt.txt")
	metricsFile := filepath.Join(base, "metrics.json")
	if err := os.WriteFile(promptFile, []byte(t.Prompt), 0o644); err != nil {
		return fmt.Errorf("write prompt: %w", err)
	}
	cmdline := strings.NewReplacer("{workdir}", wd, "{prompt_file}", promptFile, "{model}", cfg.Model, "{metrics_file}", metricsFile, "{fix}", t.Fix).Replace(cfg.AgentCmd)

	start := cfg.Clock()
	actx, cancel := context.WithTimeout(ctx, time.Duration(t.TimeoutS)*time.Second)
	r.AgentExit = shell(actx, wd, cmdline, filepath.Join(base, "agent.log"))
	cancel()
	r.WallTimeS = cfg.Clock().Sub(start).Seconds()
	readMetrics(metricsFile, r)

	d, err := agentDiff(ctx, wd, t.Commit)
	if err != nil {
		return err
	}
	r.Diff = d
	files := diff.Parse(d)
	for _, f := range files {
		for _, l := range f.Lines {
			if l.Kind != ' ' {
				r.DiffSize++
			}
		}
	}
	sel, _ := rules.Select(t.Rules)
	r.Violations = rules.Run(sel, rules.Input{
		Files:     files,
		AllowDeps: t.AllowDeps,
		Read: func(p string) []byte {
			b, _ := os.ReadFile(filepath.Join(wd, filepath.FromSlash(p)))
			return b
		},
	})
	r.RuleViolations = len(r.Violations)

	// Oracle: the fix commit's tests replace whatever the agent wrote in those files.
	if t.Fix != "" && len(t.TestFiles) > 0 {
		args := append([]string{"checkout", t.Fix, "--"}, t.TestFiles...)
		if _, err := git(ctx, wd, args...); err != nil {
			return fmt.Errorf("restore oracle tests: %w", err)
		}
	}
	tctx, tcancel := context.WithTimeout(ctx, time.Duration(t.TimeoutS)*time.Second)
	defer tcancel()
	r.TestExit = shell(tctx, filepath.Join(wd, filepath.FromSlash(t.Dir)), t.TestCmd, filepath.Join(base, "test.log"))
	r.Pass = r.TestExit == 0
	return nil
}

// agentDiff is the diff from the task commit to the working tree, including new files.
func agentDiff(ctx context.Context, wd, commit string) (string, error) {
	out, err := git(ctx, wd, "ls-files", "--others", "--exclude-standard", "-z")
	if err != nil {
		return "", err
	}
	if news := strings.Split(strings.TrimRight(string(out), "\x00"), "\x00"); len(news) > 0 && news[0] != "" {
		if _, err := git(ctx, wd, append([]string{"add", "--intent-to-add", "--"}, news...)...); err != nil {
			return "", err
		}
	}
	d, err := git(ctx, wd, "diff", "--no-color", "--no-ext-diff", commit)
	if err != nil {
		return "", err
	}
	return string(d), nil
}

func readMetrics(file string, r *Result) {
	b, err := os.ReadFile(file)
	if err != nil {
		return
	}
	var m struct{ Turns, Tokens int }
	if err := json.Unmarshal(b, &m); err != nil {
		slog.Warn("bad agent metrics", "file", file, "err", err)
		return
	}
	r.Turns, r.Tokens = m.Turns, m.Tokens
}

// shell runs cmdline with sh -c and returns its exit code (-1 if it could not start, 124 on timeout).
func shell(ctx context.Context, dir, cmdline, logFile string) int {
	lf, err := os.Create(logFile)
	if err != nil {
		slog.Error("create log", "file", logFile, "err", err)
		return -1
	}
	defer lf.Close()
	cmd := exec.CommandContext(ctx, "sh", "-c", cmdline)
	cmd.Dir = dir
	cmd.Stdout, cmd.Stderr = lf, lf
	cmd.WaitDelay = 10 * time.Second
	err = cmd.Run()
	if ctx.Err() != nil {
		return 124
	}
	var ee *exec.ExitError
	switch {
	case err == nil:
		return 0
	case errors.As(err, &ee):
		return ee.ExitCode()
	default:
		fmt.Fprintf(lf, "start: %v\n", err)
		return -1
	}
}

func git(ctx context.Context, dir string, args ...string) ([]byte, error) {
	if dir != "" {
		args = append([]string{"-C", dir}, args...)
	}
	cmd := exec.CommandContext(ctx, "git", args...)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("git %s: %w: %s", strings.Join(args, " "), err, strings.TrimSpace(stderr.String()))
	}
	return out, nil
}
