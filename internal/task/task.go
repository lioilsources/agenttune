// Package task defines an eval task (evals/tasks/<id>/task.yaml).
package task

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"

	"gopkg.in/yaml.v3"

	"github.com/lioilsources/agenttune/internal/rules"
)

// Split says whether a task may be used for tuning (dev) or only for the final gate (holdout).
type Split string

const (
	Dev     Split = "dev"
	Holdout Split = "holdout"
)

// Task is one eval task: a repo at the commit before a fix, a prompt and a test oracle.
type Task struct {
	ID        string   `yaml:"id"`
	Repo      string   `yaml:"repo"`          // owner/name on GitHub
	Commit    string   `yaml:"commit"`        // parent of the fix commit; the agent starts here
	Fix       string   `yaml:"fix,omitempty"` // the original fix commit (for data dedup, never shown to the agent)
	Lang      string   `yaml:"lang"`          // go | flutter
	Dir       string   `yaml:"dir,omitempty"` // subdirectory the test_cmd runs in
	Prompt    string   `yaml:"prompt"`
	TestCmd   string   `yaml:"test_cmd"`
	TestFiles []string `yaml:"test_files,omitempty"` // oracle tests copied from Fix after the agent finishes
	TimeoutS  int      `yaml:"timeout_s"`
	Rules     []string `yaml:"rules"`
	AllowDeps bool     `yaml:"allow_deps,omitempty"`
	Split     Split    `yaml:"split"`
}

var idRe = regexp.MustCompile(`^[a-z0-9][a-z0-9-]*-\d{3}$`)
var shaRe = regexp.MustCompile(`^[0-9a-f]{7,40}$`)

// Validate reports every problem at once.
func (t Task) Validate() error {
	var errs []error
	if !idRe.MatchString(t.ID) {
		errs = append(errs, fmt.Errorf("id %q: want <name>-NNN", t.ID))
	}
	if t.Repo == "" {
		errs = append(errs, errors.New("repo missing"))
	}
	if !shaRe.MatchString(t.Commit) {
		errs = append(errs, fmt.Errorf("commit %q is not a sha", t.Commit))
	}
	if t.Lang != "go" && t.Lang != "flutter" {
		errs = append(errs, fmt.Errorf("lang %q: want go or flutter", t.Lang))
	}
	if t.Prompt == "" || t.TestCmd == "" {
		errs = append(errs, errors.New("prompt and test_cmd are required"))
	}
	if t.TimeoutS <= 0 {
		errs = append(errs, errors.New("timeout_s must be > 0"))
	}
	if t.Split != Dev && t.Split != Holdout {
		errs = append(errs, fmt.Errorf("split %q: want dev or holdout", t.Split))
	}
	if _, unk := rules.Select(t.Rules); len(unk) > 0 {
		errs = append(errs, fmt.Errorf("unknown rules %v", unk))
	}
	return errors.Join(errs...)
}

// Load reads one task.yaml.
func Load(file string) (Task, error) {
	var t Task
	b, err := os.ReadFile(file)
	if err != nil {
		return t, fmt.Errorf("read task: %w", err)
	}
	dec := yaml.NewDecoder(bytes.NewReader(b))
	dec.KnownFields(true)
	if err := dec.Decode(&t); err != nil {
		return t, fmt.Errorf("parse %s: %w", file, err)
	}
	if err := t.Validate(); err != nil {
		return t, fmt.Errorf("invalid %s: %w", file, err)
	}
	return t, nil
}

// LoadDir reads every <dir>/*/task.yaml, sorted by ID, and rejects duplicate IDs.
func LoadDir(dir string) ([]Task, error) {
	files, err := filepath.Glob(filepath.Join(dir, "*", "task.yaml"))
	if err != nil {
		return nil, fmt.Errorf("glob tasks: %w", err)
	}
	var out []Task
	seen := map[string]bool{}
	for _, f := range files {
		t, err := Load(f)
		if err != nil {
			return nil, err
		}
		if seen[t.ID] {
			return nil, fmt.Errorf("duplicate task id %s", t.ID)
		}
		seen[t.ID] = true
		out = append(out, t)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out, nil
}

// Marshal renders a task as YAML.
func Marshal(t Task) ([]byte, error) {
	b, err := yaml.Marshal(t)
	if err != nil {
		return nil, fmt.Errorf("marshal task: %w", err)
	}
	return b, nil
}
