// Package tools 实现 9 个原子工具（Phase 3：file_read/write/patch + no_tool，带 anchor prompt + memory access log）。
package tools

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"genericagent/internal/agent"
	"genericagent/internal/loop"
	"genericagent/internal/memory"
)

func joinPath(cwd, p string) string {
	if p == "" {
		return ""
	}
	if filepath.IsAbs(p) {
		return p
	}
	return filepath.Join(cwd, p)
}

func mustRead(p string) []byte {
	b, _ := os.ReadFile(p)
	return b
}

func skipIndex(args map[string]any) bool { return toInt(args["_index"], 0) > 0 }

// ---- no_tool：LLM 未调工具时收尾，next_prompt 空 → break ----
type NoTool struct{}

func (NoTool) Name() string { return "no_tool" }
func (NoTool) Dispatch(ctx context.Context, args map[string]any, resp agent.Response) agent.Stream[agent.ToolEvent] {
	ch := make(chan agent.ToolEvent, 1)
	go func() {
		defer close(ch)
		ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: resp.Content, NextPrompt: ""}}
	}()
	return ch
}

// ---- file_write ----
type FileWrite struct{}

func (FileWrite) Name() string { return "file_write" }
func (f FileWrite) Dispatch(ctx context.Context, args map[string]any, resp agent.Response) agent.Stream[agent.ToolEvent] {
	ch := make(chan agent.ToolEvent, 4)
	hh, _ := args["_handler"].(*loop.Handler)
	go func() {
		defer close(ch)
		cwd, _ := args["_cwd"].(string)
		path := joinPath(cwd, fmt.Sprint(args["path"]))
		content, _ := args["content"].(string)
		mode, _ := args["mode"].(string)
		if mode == "" {
			mode = "overwrite"
		}
		ch <- agent.ToolEvent{Log: fmt.Sprintf("[Action] write %s (%s)\n", path, mode)}
		var err error
		switch mode {
		case "append":
			err = os.WriteFile(path, append(mustRead(path), []byte(content)...), 0o644)
		case "prepend":
			err = os.WriteFile(path, append([]byte(content), mustRead(path)...), 0o644)
		default:
			err = os.WriteFile(path, []byte(content), 0o644)
		}
		out := agent.StepOutcome{Data: map[string]any{"path": path, "mode": mode}, NextPrompt: hh.GetAnchorPrompt(skipIndex(args))}
		if err != nil {
			out.Data = map[string]any{"error": err.Error()}
		}
		ch <- agent.ToolEvent{Outcome: &out}
	}()
	return ch
}

// ---- file_read ----
type FileRead struct{}

func (FileRead) Name() string { return "file_read" }
func (f FileRead) Dispatch(ctx context.Context, args map[string]any, resp agent.Response) agent.Stream[agent.ToolEvent] {
	ch := make(chan agent.ToolEvent, 4)
	hh, _ := args["_handler"].(*loop.Handler)
	go func() {
		defer close(ch)
		cwd, _ := args["_cwd"].(string)
		root, _ := args["_root"].(string)
		path := joinPath(cwd, fmt.Sprint(args["path"]))
		start := toInt(args["start"], 1)
		count := toInt(args["count"], 200)
		showLinenos := true
		if v, ok := args["show_linenos"].(bool); ok {
			showLinenos = v
		}
		ch <- agent.ToolEvent{Log: fmt.Sprintf("[Action] read %s\n", path)}
		data, err := os.ReadFile(path)
		if err != nil {
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": err.Error()}, NextPrompt: hh.GetAnchorPrompt(skipIndex(args))}}
			return
		}
		lines := strings.Split(string(data), "\n")
		end := start + count
		if end > len(lines) {
			end = len(lines)
		}
		if start < 1 {
			start = 1
		}
		var b strings.Builder
		if showLinenos {
			b.WriteString("由于设置了show_linenos，以下返回信息为：(行号|)内容 。\n")
		}
		for i := start - 1; i < end && i < len(lines); i++ {
			if showLinenos {
				fmt.Fprintf(&b, "%4d: %s\n", i+1, lines[i])
			} else {
				b.WriteString(lines[i] + "\n")
			}
		}
		memory.LogMemoryAccess(path, root)
		nextPrompt := hh.GetAnchorPrompt(skipIndex(args))
		if strings.Contains(path, "memory") || strings.Contains(path, "sop") {
			nextPrompt += "\n[SYSTEM TIPS] 正在读取记忆或SOP文件，若决定按sop执行请提取sop中的关键点（特别是靠后的）update working memory."
		}
		ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: b.String(), NextPrompt: nextPrompt}}
	}()
	return ch
}

// ---- file_patch ----
type FilePatch struct{}

func (FilePatch) Name() string { return "file_patch" }
func (f FilePatch) Dispatch(ctx context.Context, args map[string]any, resp agent.Response) agent.Stream[agent.ToolEvent] {
	ch := make(chan agent.ToolEvent, 4)
	hh, _ := args["_handler"].(*loop.Handler)
	go func() {
		defer close(ch)
		cwd, _ := args["_cwd"].(string)
		path := joinPath(cwd, fmt.Sprint(args["path"]))
		oldC, _ := args["old_content"].(string)
		newC, _ := args["new_content"].(string)
		ch <- agent.ToolEvent{Log: fmt.Sprintf("[Action] patch %s\n", path)}
		data, err := os.ReadFile(path)
		if err != nil {
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": err.Error()}, NextPrompt: hh.GetAnchorPrompt(skipIndex(args))}}
			return
		}
		cnt := strings.Count(string(data), oldC)
		if cnt == 0 {
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": "old_content not found"}, NextPrompt: hh.GetAnchorPrompt(skipIndex(args))}}
			return
		}
		if cnt > 1 {
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": fmt.Sprintf("old_content not unique (%d matches)", cnt)}, NextPrompt: hh.GetAnchorPrompt(skipIndex(args))}}
			return
		}
		patched := strings.Replace(string(data), oldC, newC, 1)
		if err := os.WriteFile(path, []byte(patched), 0o644); err != nil {
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": err.Error()}, NextPrompt: hh.GetAnchorPrompt(skipIndex(args))}}
			return
		}
		ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"patched": path}, NextPrompt: hh.GetAnchorPrompt(skipIndex(args))}}
	}()
	return ch
}

func toInt(v any, def int) int {
	switch t := v.(type) {
	case float64:
		return int(t)
	case int:
		return t
	case string:
		if n, err := strconv.Atoi(t); err == nil {
			return n
		}
	}
	return def
}
