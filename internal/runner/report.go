package runner

import (
	"fmt"
	"io"
	"sort"

	"github.com/lioilsources/agenttune/internal/task"
)

// Summary aggregates results of one split.
type Summary struct {
	Split          task.Split `json:"split"`
	Tasks          int        `json:"tasks"`
	Errors         int        `json:"errors"`
	PassAt1        float64    `json:"pass_at_1"`
	MeanViolations float64    `json:"mean_violations"`
	MedianTurns    float64    `json:"median_turns"`
	MedianTokens   float64    `json:"median_tokens"`
	MedianWallS    float64    `json:"median_wall_s"`
}

// Summarize groups results by split. Tasks with an infrastructure error count as failed
// for pass@1 but are excluded from the other means, and are reported separately.
func Summarize(rs []Result) []Summary {
	by := map[task.Split][]Result{}
	for _, r := range rs {
		by[r.Split] = append(by[r.Split], r)
	}
	var out []Summary
	for _, sp := range []task.Split{task.Dev, task.Holdout} {
		g := by[sp]
		if len(g) == 0 {
			continue
		}
		s := Summary{Split: sp, Tasks: len(g)}
		var pass, viol int
		var turns, tokens, wall []float64
		for _, r := range g {
			if r.Error != "" {
				s.Errors++
				continue
			}
			if r.Pass {
				pass++
			}
			viol += r.RuleViolations
			turns = append(turns, float64(r.Turns))
			tokens = append(tokens, float64(r.Tokens))
			wall = append(wall, r.WallTimeS)
		}
		s.PassAt1 = float64(pass) / float64(len(g))
		if ok := len(g) - s.Errors; ok > 0 {
			s.MeanViolations = float64(viol) / float64(ok)
		}
		s.MedianTurns, s.MedianTokens, s.MedianWallS = median(turns), median(tokens), median(wall)
		out = append(out, s)
	}
	return out
}

func median(v []float64) float64 {
	if len(v) == 0 {
		return 0
	}
	s := append([]float64(nil), v...)
	sort.Float64s(s)
	m := len(s) / 2
	if len(s)%2 == 1 {
		return s[m]
	}
	return (s[m-1] + s[m]) / 2
}

// WriteMarkdown writes the summary and per-task table.
func WriteMarkdown(w io.Writer, model string, rs []Result) error {
	p := func(format string, a ...any) {
		fmt.Fprintf(w, format, a...)
	}
	p("# Eval: %s\n\n", model)
	p("| split | tasks | errors | pass@1 | mean violations | median turns | median tokens | median wall s |\n|---|---:|---:|---:|---:|---:|---:|---:|\n")
	for _, s := range Summarize(rs) {
		p("| %s | %d | %d | %.2f | %.2f | %.0f | %.0f | %.0f |\n", s.Split, s.Tasks, s.Errors, s.PassAt1, s.MeanViolations, s.MedianTurns, s.MedianTokens, s.MedianWallS)
	}
	p("\n| task | split | pass | violations | turns | tokens | wall s | diff | note |\n|---|---|---|---:|---:|---:|---:|---:|---|\n")
	for _, r := range rs {
		note := r.Error
		if note == "" && r.AgentExit != 0 {
			note = fmt.Sprintf("agent exit %d", r.AgentExit)
		}
		p("| %s | %s | %v | %d | %d | %d | %.0f | %d | %s |\n", r.Task, r.Split, r.Pass, r.RuleViolations, r.Turns, r.Tokens, r.WallTimeS, r.DiffSize, note)
	}
	_, err := fmt.Fprint(w, "")
	return err
}
