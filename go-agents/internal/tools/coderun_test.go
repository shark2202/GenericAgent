package tools

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"genericagent/internal/agent"
	"genericagent/internal/loop"
)

func newTestHandler() *loop.Handler {
	return loop.NewHandler(".", ".", "", map[string]agent.Tool{})
}

func TestCodeRunPython(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}
	cr := CodeRun{}
	args := map[string]any{
		"type":     "python",
		"code":     "print(sum(range(10)))",
		"_cwd":     ".",
		"_handler": newTestHandler(),
	}
	ch := cr.Dispatch(context.Background(), args, agent.Response{})
	var outcome *agent.StepOutcome
	for ev := range ch {
		if ev.Outcome != nil {
			outcome = ev.Outcome
		}
	}
	if outcome == nil {
		t.Fatal("no outcome")
	}
	data, _ := outcome.Data.(map[string]any)
	out, _ := data["output"].(string)
	if out != "45" {
		t.Fatalf("expected 45, got %q (full=%v)", out, data)
	}
}

func TestCodeRunBash(t *testing.T) {
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("bash not available")
	}
	cr := CodeRun{}
	args := map[string]any{"type": "bash", "code": "echo hi", "_cwd": ".", "_handler": newTestHandler()}
	ch := cr.Dispatch(context.Background(), args, agent.Response{})
	var outcome *agent.StepOutcome
	for ev := range ch {
		if ev.Outcome != nil {
			outcome = ev.Outcome
		}
	}
	data, _ := outcome.Data.(map[string]any)
	if data["output"] != "hi" {
		t.Fatalf("expected hi, got %v", data["output"])
	}
}

// TestCodeRunUsesBundlePython 验证 code_run 经 python.Resolve 用 bundle 解释器（dev bundle 存在时）。
// 对应 ponytail「一条可运行检查」：resolver → exec → bundle python 链路。
func TestCodeRunUsesBundlePython(t *testing.T) {
	abs, err := filepath.Abs(filepath.Join("..", "..", "python-bundle", "python", "bin", "python3"))
	if err != nil {
		t.Skip("bundle path error: " + err.Error())
	}
	if _, err := os.Stat(abs); err != nil {
		t.Skip("bundle not built: run ./scripts/bundle-python.sh")
	}
	t.Setenv("GA_PYTHON", abs)
	outFile := filepath.Join(t.TempDir(), "pybin.txt")
	code := fmt.Sprintf("import sys; open('%s','w').write(sys.executable)", outFile)
	cr := CodeRun{}
	args := map[string]any{"type": "python", "code": code, "_cwd": ".", "_handler": newTestHandler()}
	ch := cr.Dispatch(context.Background(), args, agent.Response{})
	for ev := range ch {
		if ev.Outcome != nil {
			if d, ok := ev.Outcome.Data.(map[string]any); ok {
				if e, _ := d["error"].(string); e != "" && e != "timeout" {
					t.Fatalf("code_run error: %v", d)
				}
			}
		}
	}
	got, err := os.ReadFile(outFile)
	if err != nil {
		t.Fatal("pybin file not written (code_run did not use bundle)")
	}
	if strings.TrimSpace(string(got)) != abs {
		t.Fatalf("code_run used %q, want bundle %q", string(got), abs)
	}
}

func TestCodeRunTimeout(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	cr := CodeRun{}
	args := map[string]any{"type": "python", "code": "import time; time.sleep(10)", "_cwd": ".", "timeout": 1, "_handler": newTestHandler()}
	ch := cr.Dispatch(ctx, args, agent.Response{})
	var outcome *agent.StepOutcome
	for ev := range ch {
		if ev.Outcome != nil {
			outcome = ev.Outcome
		}
	}
	data, _ := outcome.Data.(map[string]any)
	if data["error"] != "timeout" {
		t.Fatalf("expected timeout, got %v", data)
	}
}
