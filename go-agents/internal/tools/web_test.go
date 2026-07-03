package tools

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestSmartFormat(t *testing.T) {
	if smartFormat("short", 100) != "short" {
		t.Fatal("short should be unchanged")
	}
	s := strings.Repeat("x", 300)
	out := smartFormat(s, 100)
	if !strings.Contains(out, " ... ") || len(out) >= 300 {
		t.Fatalf("not truncated: len=%d", len(out))
	}
	zh := strings.Repeat("中", 300)
	out2 := smartFormat(zh, 100)
	if !strings.Contains(out2, " ... ") {
		t.Fatal("unicode not truncated")
	}
}

func TestExtractCodeBlock(t *testing.T) {
	if got := extractCodeBlock("text\n```javascript\nreturn 1+1\n```\nmore", "javascript"); got != "return 1+1" {
		t.Fatalf("got %q", got)
	}
	if got := extractCodeBlock("no block", "javascript"); got != "" {
		t.Fatalf("expected empty, got %q", got)
	}
	c2 := "```javascript\na\n```\n```javascript\nb\n```"
	if got := extractCodeBlock(c2, "javascript"); got != "b" {
		t.Fatalf("expected last match b, got %q", got)
	}
}

func TestBridgeMock(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}
	mock := `import sys, json
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    cmd=json.loads(line)
    c=cmd.get("cmd")
    if c=="ping": r={"status":"ok"}
    elif c=="scan": r={"status":"success","metadata":{"tabs_count":1},"content":"<html></html>"}
    elif c=="execute_js": r={"status":"success","js_return":cmd.get("script")}
    else: r={"status":"error","msg":"unknown"}
    sys.stdout.write(json.dumps(r)+"\n"); sys.stdout.flush()
`
	f, err := os.CreateTemp("", "mock-bridge-*.py")
	if err != nil {
		t.Fatal(err)
	}
	f.WriteString(mock)
	f.Close()
	defer os.Remove(f.Name())

	b := &Bridge{ScriptPath: f.Name(), Root: "."}
	defer b.Close()

	out, err := b.Call(map[string]any{"cmd": "ping"})
	if err != nil {
		t.Fatal(err)
	}
	if out["status"] != "ok" {
		t.Fatalf("ping: %v", out)
	}
	out, err = b.Call(map[string]any{"cmd": "scan", "maxlen": 1000})
	if err != nil {
		t.Fatal(err)
	}
	if out["content"] != "<html></html>" {
		t.Fatalf("scan content: %v", out["content"])
	}
	out, err = b.Call(map[string]any{"cmd": "execute_js", "script": "return 42"})
	if err != nil {
		t.Fatal(err)
	}
	if out["js_return"] != "return 42" {
		t.Fatalf("js_return: %v", out["js_return"])
	}
}
