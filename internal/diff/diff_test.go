package diff

import "testing"

const sample = `diff --git a/x.go b/x.go
index 1..2 100644
--- a/x.go
+++ b/x.go
@@ -10,3 +10,4 @@ func f() error {
 	a()
-	return err
+	return fmt.Errorf("f: %w", err)
+	// done
 }
diff --git a/old.go b/old.go
deleted file mode 100644
--- a/old.go
+++ /dev/null
@@ -1 +0,0 @@
-package old
`

func TestParse(t *testing.T) {
	fs := Parse(sample)
	if len(fs) != 2 {
		t.Fatalf("files = %d, want 2", len(fs))
	}
	if fs[0].Path != "x.go" || fs[1].Path != "" || fs[1].OldPath != "old.go" {
		t.Fatalf("paths = %q %q %q", fs[0].Path, fs[1].Path, fs[1].OldPath)
	}
	add := fs[0].Added()
	if len(add) != 2 || add[0].N != 11 || add[1].N != 12 {
		t.Fatalf("added = %+v", add)
	}
}
