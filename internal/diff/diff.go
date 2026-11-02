// Package diff parses unified diffs (git diff output) into per-file added and removed lines.
package diff

import (
	"bufio"
	"strconv"
	"strings"
)

// Line is one line of a hunk with its number in the new file (0 for removed lines).
type Line struct {
	N    int
	Text string
	Kind byte // '+', '-' or ' '
}

// File is the change to one path. Path is the new path ("" when the file was deleted).
type File struct {
	Path    string
	OldPath string
	Lines   []Line
}

// Added returns only the added lines.
func (f File) Added() []Line {
	var out []Line
	for _, l := range f.Lines {
		if l.Kind == '+' {
			out = append(out, l)
		}
	}
	return out
}

// Parse reads git-style unified diff text.
func Parse(text string) []File {
	var files []File
	var cur *File
	n := 0
	sc := bufio.NewScanner(strings.NewReader(text))
	sc.Buffer(make([]byte, 1024*1024), 16*1024*1024)
	for sc.Scan() {
		s := sc.Text()
		switch {
		case strings.HasPrefix(s, "diff --git "):
			files = append(files, File{})
			cur = &files[len(files)-1]
		case cur == nil:
			continue
		case strings.HasPrefix(s, "--- "):
			cur.OldPath = trimPath(s[4:])
		case strings.HasPrefix(s, "+++ "):
			cur.Path = trimPath(s[4:])
		case strings.HasPrefix(s, "@@"):
			n = hunkStart(s)
		case strings.HasPrefix(s, "+"):
			cur.Lines = append(cur.Lines, Line{N: n, Text: s[1:], Kind: '+'})
			n++
		case strings.HasPrefix(s, "-"):
			cur.Lines = append(cur.Lines, Line{Text: s[1:], Kind: '-'})
		case strings.HasPrefix(s, " "):
			cur.Lines = append(cur.Lines, Line{N: n, Text: s[1:], Kind: ' '})
			n++
		}
	}
	return files
}

func trimPath(p string) string {
	if p == "/dev/null" {
		return ""
	}
	if i := strings.IndexByte(p, '\t'); i >= 0 {
		p = p[:i]
	}
	if strings.HasPrefix(p, "a/") || strings.HasPrefix(p, "b/") {
		return p[2:]
	}
	return p
}

// hunkStart returns the new-file start line from "@@ -a,b +c,d @@".
func hunkStart(s string) int {
	i := strings.Index(s, " +")
	if i < 0 {
		return 0
	}
	rest := s[i+2:]
	end := strings.IndexAny(rest, ", ")
	if end < 0 {
		end = len(rest)
	}
	v, err := strconv.Atoi(rest[:end])
	if err != nil {
		return 0
	}
	return v
}
