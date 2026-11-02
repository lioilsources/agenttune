// Package candidates finds commits that change code and tests together: each is a
// possible eval task where the added tests are the oracle.
package candidates

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"regexp"
	"strings"
)

// Commit is one candidate.
type Commit struct {
	SHA       string   `json:"sha"`
	Parent    string   `json:"parent"`
	Date      string   `json:"date"`
	Subject   string   `json:"subject"`
	CodeFiles []string `json:"code_files"`
	TestFiles []string `json:"test_files"`
	Tests     []string `json:"tests"` // Go test functions added in the commit
}

func isTest(p string) bool {
	return strings.HasSuffix(p, "_test.go") || strings.HasSuffix(p, "_test.dart")
}
func isCode(p string) bool {
	return (strings.HasSuffix(p, ".go") || strings.HasSuffix(p, ".dart")) && !isTest(p) &&
		!strings.HasSuffix(p, ".g.dart") && !strings.HasSuffix(p, ".freezed.dart")
}

// Find lists non-merge commits in repo that touch both code and test files,
// newest first, at most max (0 = all).
func Find(ctx context.Context, repo string, max int) ([]Commit, error) {
	out, err := git(ctx, repo, "log", "--no-merges", "--format=@%H\t%P\t%cs\t%s", "--name-only")
	if err != nil {
		return nil, err
	}
	var res []Commit
	var cur *Commit
	flush := func() {
		if cur != nil && len(cur.CodeFiles) > 0 && len(cur.TestFiles) > 0 && !strings.Contains(cur.Parent, " ") && cur.Parent != "" {
			res = append(res, *cur)
		}
		cur = nil
	}
	sc := bufio.NewScanner(bytes.NewReader(out))
	sc.Buffer(make([]byte, 1024*1024), 64*1024*1024)
	for sc.Scan() {
		line := sc.Text()
		if strings.HasPrefix(line, "@") {
			flush()
			parts := strings.SplitN(line[1:], "\t", 4)
			if len(parts) < 4 {
				continue
			}
			cur = &Commit{SHA: parts[0], Parent: parts[1], Date: parts[2], Subject: parts[3]}
			continue
		}
		if cur == nil || line == "" {
			continue
		}
		switch {
		case isTest(line):
			cur.TestFiles = append(cur.TestFiles, line)
		case isCode(line):
			cur.CodeFiles = append(cur.CodeFiles, line)
		}
	}
	flush()
	if max > 0 && len(res) > max {
		res = res[:max]
	}
	for i := range res {
		names, err := addedGoTests(ctx, repo, res[i])
		if err != nil {
			return nil, err
		}
		res[i].Tests = names
	}
	return res, nil
}

var goTestFunc = regexp.MustCompile(`^\+func (Test[A-Za-z0-9_]+)\(`)

func addedGoTests(ctx context.Context, repo string, c Commit) ([]string, error) {
	var goTests []string
	for _, f := range c.TestFiles {
		if strings.HasSuffix(f, "_test.go") {
			goTests = append(goTests, f)
		}
	}
	if len(goTests) == 0 {
		return nil, nil
	}
	args := append([]string{"diff", "--no-color", c.Parent, c.SHA, "--"}, goTests...)
	out, err := git(ctx, repo, args...)
	if err != nil {
		return nil, err
	}
	var names []string
	for _, l := range strings.Split(string(out), "\n") {
		if m := goTestFunc.FindStringSubmatch(l); m != nil {
			names = append(names, m[1])
		}
	}
	return names, nil
}

func git(ctx context.Context, repo string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, "git", append([]string{"-C", repo}, args...)...)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("git %s: %w: %s", args[0], err, strings.TrimSpace(stderr.String()))
	}
	return out, nil
}
