package python

import (
	"os"
	"testing"
)

func TestResolveEnvOverride(t *testing.T) {
	f, err := os.CreateTemp("", "pyshim-*.bin")
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
	defer os.Remove(f.Name())
	t.Setenv("GA_PYTHON", f.Name())
	if got := Resolve(""); got != f.Name() {
		t.Fatalf("env override: got %q want %q", got, f.Name())
	}
}

func TestResolveFallbackNonEmpty(t *testing.T) {
	t.Setenv("GA_PYTHON", "")
	if got := Resolve(""); got == "" {
		t.Fatal("resolve returned empty")
	}
}
