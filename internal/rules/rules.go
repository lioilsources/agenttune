// Package rules holds executable checks over a diff. Each rule comes from a written
// convention (DevConventions shared modules or the agenttune plan); Source says which.
package rules

import (
	"path"
	"regexp"
	"sort"
	"strings"

	"github.com/lioilsources/agenttune/internal/diff"
)

// Violation is one broken rule at one place.
type Violation struct {
	Rule string `json:"rule"`
	File string `json:"file"`
	Line int    `json:"line,omitempty"`
	Msg  string `json:"msg"`
}

// Input is what a rule sees. Read returns the new content of a file in the worktree
// (nil when unavailable); AllowDeps is set when the task explicitly asks for a dependency.
type Input struct {
	Files     []diff.File
	Read      func(path string) []byte
	AllowDeps bool
}

// Rule is a named check.
type Rule struct {
	ID     string
	Source string
	Check  func(Input) []Violation
}

var all = []Rule{
	{"go-errors-wrap", "shared/stacks/go.md", goErrorsWrap},
	{"no-fmt-println", "agenttune plan §2 F1", noFmtPrintln},
	{"go-test-no-time-now", "shared/stacks/go.md", goTestNoTimeNow},
	{"no-edit-migrations", "shared/stacks/go.md", noEditMigrations},
	{"no-edit-generated", "shared/stacks/go.md", noEditGenerated},
	{"no-new-deps", "agenttune plan §2 F1", noNewDeps},
	{"tests-added", "agenttune plan §2 F1, §7", testsAdded},
	{"flutter-no-hardcoded-text", "shared/stacks/flutter.md", flutterHardcodedText},
	{"flutter-no-logic-in-build", "agenttune plan §2 F1", flutterLogicInBuild},
	{"no-release-workflow-edit", "shared/stacks/flutter.md", noReleaseWorkflowEdit},
	{"no-secrets-files", "shared/git.md", noSecretsFiles},
}

// All returns every rule, sorted by ID.
func All() []Rule {
	out := append([]Rule(nil), all...)
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// Select returns the rules with the given IDs; unknown IDs are returned separately.
func Select(ids []string) (sel []Rule, unknown []string) {
	byID := map[string]Rule{}
	for _, r := range all {
		byID[r.ID] = r
	}
	for _, id := range ids {
		if r, ok := byID[id]; ok {
			sel = append(sel, r)
		} else {
			unknown = append(unknown, id)
		}
	}
	return sel, unknown
}

// Run applies rules to the input.
func Run(rs []Rule, in Input) []Violation {
	if in.Read == nil {
		in.Read = func(string) []byte { return nil }
	}
	var out []Violation
	for _, r := range rs {
		out = append(out, r.Check(in)...)
	}
	return out
}

func isGo(p string) bool     { return strings.HasSuffix(p, ".go") }
func isGoTest(p string) bool { return strings.HasSuffix(p, "_test.go") }
func isDart(p string) bool   { return strings.HasSuffix(p, ".dart") }
func isDartTest(p string) bool {
	return strings.HasSuffix(p, "_test.dart") || strings.HasPrefix(p, "test/") || strings.Contains(p, "/test/")
}

// generated reports whether a file is generated code (by path or by its header).
func generated(in Input, p string) bool {
	if generatedPath.MatchString(p) {
		return true
	}
	head := string(in.Read(p))
	if len(head) > 2048 {
		head = head[:2048]
	}
	return strings.Contains(head, "Code generated") && strings.Contains(head, "DO NOT EDIT")
}

// added calls fn for every added line of hand-written files that keep accepts.
func added(in Input, keep func(string) bool, fn func(f diff.File, l diff.Line)) {
	for _, f := range in.Files {
		if f.Path == "" || !keep(f.Path) || generated(in, f.Path) {
			continue
		}
		for _, l := range f.Added() {
			fn(f, l)
		}
	}
}

func stripComment(s string) string {
	if i := strings.Index(s, "//"); i >= 0 {
		return s[:i]
	}
	return s
}

var bareErrReturn = regexp.MustCompile(`^\s*return\s+(?:[^,]+,\s*)*err\s*$`)
var errAssign = regexp.MustCompile(`\berr\s*:?=\s*(.*)$`)
var qualifiedCall = regexp.MustCompile(`^\s*&?[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*\.[A-Za-z_][a-zA-Z0-9_]*\(`)

// goErrorsWrap flags an added bare `return …, err` when the nearest preceding err
// assignment in the hunk is a call on another package or value (pkg.F(), x.M()).
// Errors from same-package calls may pass through; unknown origin is not flagged.
func goErrorsWrap(in Input) []Violation {
	var out []Violation
	for _, f := range in.Files {
		if f.Path == "" || !isGo(f.Path) || isGoTest(f.Path) || generated(in, f.Path) {
			continue
		}
		origin := ""
		for _, l := range f.Lines {
			if l.Kind == '-' {
				continue
			}
			s := stripComment(l.Text)
			if m := errAssign.FindStringSubmatch(s); m != nil && !strings.Contains(s, "fmt.Errorf") {
				origin = m[1]
				continue
			}
			if l.Kind == '+' && bareErrReturn.MatchString(s) && qualifiedCall.MatchString(origin) {
				out = append(out, Violation{"go-errors-wrap", f.Path, l.N, "bare `return err` from " + strings.TrimSpace(origin) + "; wrap it with fmt.Errorf(\"…: %w\", err)"})
			}
		}
	}
	return out
}

func noFmtPrintln(in Input) []Violation {
	var out []Violation
	keep := func(p string) bool {
		return isGo(p) && !isGoTest(p) && path.Base(p) != "main.go" && !strings.HasPrefix(p, "cmd/") && !strings.Contains(p, "/cmd/")
	}
	added(in, keep, func(f diff.File, l diff.Line) {
		if strings.Contains(stripComment(l.Text), "fmt.Println(") {
			out = append(out, Violation{"no-fmt-println", f.Path, l.N, "fmt.Println outside main/cmd; use slog"})
		}
	})
	return out
}

func goTestNoTimeNow(in Input) []Violation {
	var out []Violation
	added(in, isGoTest, func(f diff.File, l diff.Line) {
		if strings.Contains(stripComment(l.Text), "time.Now()") {
			out = append(out, Violation{"go-test-no-time-now", f.Path, l.N, "test depends on time.Now; inject a clock"})
		}
	})
	return out
}

func isMigration(p string) bool {
	return strings.Contains("/"+p, "/migrations/") && (strings.HasSuffix(p, ".sql") || strings.HasSuffix(p, ".go"))
}

func noEditMigrations(in Input) []Violation {
	var out []Violation
	for _, f := range in.Files {
		p := f.OldPath
		if p == "" || !isMigration(p) {
			continue // new migration files are fine
		}
		out = append(out, Violation{"no-edit-migrations", p, 0, "existing migration changed; add a new one instead"})
	}
	return out
}

var generatedPath = regexp.MustCompile(`(\.g\.dart|\.freezed\.dart|\.pb\.go|\.sql\.go|_gen\.go)$`)

// noEditGenerated flags changes to generated files unless the diff also changes a
// generator source (.sql, .proto, sqlc/build config, or the Dart part's library), i.e. a regeneration.
func noEditGenerated(in Input) []Violation {
	changed := map[string]bool{}
	sources := false
	for _, f := range in.Files {
		changed[f.Path] = true
		b := path.Base(f.Path)
		if strings.HasSuffix(b, ".sql") || strings.HasSuffix(b, ".proto") || strings.HasPrefix(b, "sqlc.") || b == "build.yaml" || b == "pubspec.yaml" {
			sources = true
		}
	}
	var out []Violation
	for _, f := range in.Files {
		if f.Path == "" || f.OldPath == "" || len(f.Added()) == 0 || !generated(in, f.Path) {
			continue
		}
		if sources {
			continue
		}
		if lib := strings.TrimSuffix(strings.TrimSuffix(f.Path, ".g.dart"), ".freezed.dart"); lib != f.Path && changed[lib+".dart"] {
			continue
		}
		out = append(out, Violation{"no-edit-generated", f.Path, 0, "generated file edited by hand; change the source and regenerate"})
	}
	return out
}

var dartDepLine = regexp.MustCompile(`^\s{2}[a-z0-9_]+:`)

func noNewDeps(in Input) []Violation {
	if in.AllowDeps {
		return nil
	}
	var out []Violation
	for _, f := range in.Files {
		switch path.Base(f.Path) {
		case "go.mod":
			for _, l := range f.Added() {
				t := strings.TrimSpace(l.Text)
				if strings.HasPrefix(t, "require ") || (strings.Contains(t, " v") && !strings.HasPrefix(t, "go ") && !strings.HasPrefix(t, "module ") && !strings.HasSuffix(t, "// indirect")) {
					out = append(out, Violation{"no-new-deps", f.Path, l.N, "new dependency without explicit request: " + t})
				}
			}
		case "pubspec.yaml":
			sections := pubspecSections(in.Read(f.Path))
			for _, l := range f.Added() {
				sec := sections[l.N]
				if sec == "" {
					sec = hunkSection(f, l.N)
				}
				if dartDepLine.MatchString(l.Text) && (sec == "dependencies" || sec == "dev_dependencies") {
					out = append(out, Violation{"no-new-deps", f.Path, l.N, "pubspec dependency changed without explicit request: " + strings.TrimSpace(l.Text)})
				}
			}
		}
	}
	return out
}

var topKey = regexp.MustCompile(`^([a-z_]+):`)

// pubspecSections maps 1-based line numbers of the new pubspec.yaml to their top-level key.
func pubspecSections(b []byte) map[int]string {
	out := map[int]string{}
	cur := ""
	for i, line := range strings.Split(string(b), "\n") {
		if m := topKey.FindStringSubmatch(line); m != nil {
			cur = m[1]
		}
		out[i+1] = cur
	}
	return out
}

// hunkSection finds the top-level key above line n using only the diff (when the file is unreadable).
func hunkSection(f diff.File, n int) string {
	cur := ""
	for _, l := range f.Lines {
		if l.Kind == '-' {
			continue
		}
		if l.N > n {
			break
		}
		if m := topKey.FindStringSubmatch(l.Text); m != nil {
			cur = m[1]
		}
	}
	return cur
}

var assertion = regexp.MustCompile(`\bt\.(Error|Errorf|Fatal|Fatalf|Fail)\b|\b(assert|require)\.[A-Z]|\bexpect\(|\bexpectLater\(|\bcmp\.Diff\(`)

func testsAdded(in Input) []Violation {
	code, asserts := false, false
	for _, f := range in.Files {
		if f.Path == "" {
			continue
		}
		test := isGoTest(f.Path) || (isDart(f.Path) && isDartTest(f.Path))
		if !test && (isGo(f.Path) || isDart(f.Path)) && len(f.Added()) > 0 {
			code = true
		}
		if test {
			for _, l := range f.Added() {
				if assertion.MatchString(l.Text) {
					asserts = true
				}
			}
		}
	}
	if code && !asserts {
		return []Violation{{"tests-added", "", 0, "code changed but no added test line asserts anything"}}
	}
	return nil
}

var hardText = regexp.MustCompile(`\bText\(\s*['"][^'"$]*[A-Za-zÀ-ž]{2,}`)

func flutterHardcodedText(in Input) []Violation {
	var out []Violation
	// Only apps that use ARB l10n (l10n.yaml next to pubspec.yaml) are checked.
	keep := func(p string) bool {
		if !isDart(p) || isDartTest(p) {
			return false
		}
		root, ok := appRoot(p)
		return ok && in.Read(root+"l10n.yaml") != nil
	}
	added(in, keep, func(f diff.File, l diff.Line) {
		if hardText.MatchString(stripComment(l.Text)) {
			out = append(out, Violation{"flutter-no-hardcoded-text", f.Path, l.N, "user-facing literal in Text(); use ARB l10n"})
		}
	})
	return out
}

// appRoot returns the Flutter app directory ("" or "app/") of a file under lib/.
func appRoot(p string) (string, bool) {
	if strings.HasPrefix(p, "lib/") {
		return "", true
	}
	if i := strings.Index(p, "/lib/"); i >= 0 {
		return p[:i+1], true
	}
	return "", false
}

var buildStart = regexp.MustCompile(`\bWidget\s+build\s*\(\s*BuildContext`)
var ioInBuild = regexp.MustCompile(`\bhttp\.(get|post|put|delete)\(|\bdio\.(get|post|put|delete)\(|\.rawQuery\(|\bFile\(.*\)\.read|\bSharedPreferences\.getInstance\(|\bfuture:\s*[a-zA-Z_.]+\(`)

// flutterLogicInBuild tracks build() bodies through hunk lines (context and added) by brace depth.
func flutterLogicInBuild(in Input) []Violation {
	var out []Violation
	for _, f := range in.Files {
		if f.Path == "" || !isDart(f.Path) || isDartTest(f.Path) {
			continue
		}
		depth, inBuild := 0, false
		for _, l := range f.Lines {
			if l.Kind == '-' {
				continue
			}
			t := stripComment(l.Text)
			if !inBuild && buildStart.MatchString(t) {
				inBuild, depth = true, 0
				if strings.Contains(t, "{") {
					depth = strings.Count(t, "{") - strings.Count(t, "}")
					continue
				}
			}
			if inBuild {
				// Only the build body itself (depth 1); callbacks open their own braces.
				if loc := ioInBuild.FindStringIndex(t); l.Kind == '+' && loc != nil && !strings.Contains(t[:loc[0]], "=>") &&
					depth+strings.Count(t[:loc[0]], "{")-strings.Count(t[:loc[0]], "}") == 1 {
					out = append(out, Violation{"flutter-no-logic-in-build", f.Path, l.N, "I/O started inside build(); move it to initState or a controller and keep the Future in state"})
				}
				depth += strings.Count(t, "{") - strings.Count(t, "}")
				if depth <= 0 && strings.Contains(t, "}") {
					inBuild = false
				}
			}
		}
	}
	return out
}

func noReleaseWorkflowEdit(in Input) []Violation {
	var out []Violation
	for _, f := range in.Files {
		p := f.Path
		if p == "" {
			p = f.OldPath
		}
		if strings.HasPrefix(p, ".github/workflows/release-") {
			out = append(out, Violation{"no-release-workflow-edit", p, 0, "release workflow is derived from Distribution; fix it there"})
		}
	}
	return out
}

var secretFile = regexp.MustCompile(`(^|/)(\.env(\.[a-z]+)?|key\.properties|secrets\.dart|[^/]+\.(p8|p12|jks|pem))$`)

func noSecretsFiles(in Input) []Violation {
	var out []Violation
	for _, f := range in.Files {
		if f.Path != "" && secretFile.MatchString(f.Path) && !strings.HasSuffix(f.Path, ".env.example") {
			out = append(out, Violation{"no-secrets-files", f.Path, 0, "secret-bearing file in diff"})
		}
	}
	return out
}
