package tools

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"genericagent/internal/agent"
	"genericagent/internal/loop"
	"genericagent/internal/python"
)

// CodeRun 执行 LLM 生成的代码（python/bash）。Phase 1：A 档 subprocess，同步执行。
// 对应 Python ga.code_run。流式 stdout 留 Phase 1.2。
type CodeRun struct{}

func (CodeRun) Name() string { return "code_run" }
func (CodeRun) Dispatch(ctx context.Context, args map[string]any, resp agent.Response) agent.Stream[agent.ToolEvent] {
	ch := make(chan agent.ToolEvent, 8)
	hh, _ := args["_handler"].(*loop.Handler)
	anchor := func() string { return hh.GetAnchorPrompt(skipIndex(args)) }
	go func() {
		defer close(ch)
		cwd, _ := args["_cwd"].(string)
		root, _ := args["_root"].(string)
		codeType, _ := args["type"].(string)
		if codeType == "" {
			codeType = "python"
		}
		code, _ := args["code"].(string)
		if code == "" {
			code, _ = args["script"].(string)
		}
		if code == "" {
			code = extractCodeBlock(resp.Content, codeType) // 对应 Python _extract_code_block
		}
		timeout := time.Duration(toInt(args["timeout"], 60)) * time.Second
		ch <- agent.ToolEvent{Log: fmt.Sprintf("[Action] Running %s: %s\n", codeType, preview(code))}

		var cmd *exec.Cmd
		switch codeType {
		case "python", "py":
			tmp, err := os.CreateTemp(cwd, ".ai-*.py")
			if err != nil {
				ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": err.Error()}, NextPrompt: anchor()}}
				return
			}
			tmp.WriteString(code)
			tmp.Close()
			defer os.Remove(tmp.Name())
			c := exec.CommandContext(ctx, python.Resolve(root), "-X", "utf8", "-u", tmp.Name())
			c.Dir = cwd
			cmd = c
		case "bash", "sh", "shell":
			c := exec.CommandContext(ctx, "bash", "-c", code)
			c.Dir = cwd
			cmd = c
		case "powershell", "ps1", "pwsh":
			ps := "pwsh"
			if _, err := exec.LookPath("pwsh"); err != nil {
				ps = "powershell"
			}
			c := exec.CommandContext(ctx, ps, "-NoProfile", "-NonInteractive", "-Command", code)
			c.Dir = cwd
			cmd = c
		default:
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": "unsupported type: " + codeType}, NextPrompt: anchor()}}
			return
		}

		var out bytes.Buffer
		cmd.Stdout = &out
		cmd.Stderr = &out
		if err := cmd.Start(); err != nil {
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": err.Error()}, NextPrompt: anchor()}}
			return
		}
		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		select {
		case err := <-done:
			result := strings.TrimSpace(out.String())
			if result == "" {
				result = "(no output)"
			}
			data := map[string]any{"output": result}
			if err != nil {
				data["exit"] = err.Error()
			}
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: data, NextPrompt: anchor()}}
		case <-time.After(timeout):
			if cmd.Process != nil {
				cmd.Process.Kill()
			}
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": "timeout"}, NextPrompt: anchor()}}
		}
	}()
	return ch
}

func preview(code string) string {
	s := strings.ReplaceAll(code, "\n", " ")
	if len(s) > 60 {
		return s[:60] + "..."
	}
	return s
}

// LoadToolSchema 从 assets/tools_schema.json 加载，非 Windows 把 powershell 替换为 bash。
// 对应 Python agentmain.load_tool_schema。
func LoadToolSchema(root, lang string) ([]agent.ToolSpec, error) {
	suffix := ""
	if lang == "en" {
		suffix = "_en"
	}
	_ = suffix // schema 文件名无 _en 变体，沿用主文件
	path := filepath.Join(root, "assets", "tools_schema.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if runtime.GOOS != "windows" {
		data = bytes.ReplaceAll(data, []byte("powershell"), []byte("bash"))
	}
	var specs []agent.ToolSpec
	if err := json.Unmarshal(data, &specs); err != nil {
		return nil, err
	}
	return specs, nil
}
