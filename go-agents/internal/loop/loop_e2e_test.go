package loop_test

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"genericagent/internal/agent"
	"genericagent/internal/llm"
	"genericagent/internal/loop"
	"genericagent/internal/memory"
	"genericagent/internal/tools"
)

// setupE2ERoot 建隔离 temp root + 最小 assets，供 RunLoop 端到端测试。
func setupE2ERoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	for _, p := range []string{"memory", "temp", "assets"} {
		_ = os.MkdirAll(filepath.Join(root, p), 0o755)
	}
	_ = os.WriteFile(filepath.Join(root, "assets", "sys_prompt.txt"), []byte("You are an agent.\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "assets", "insight_fixed_structure.txt"), []byte("L1\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "assets", "global_mem_insight_template.txt"), []byte("# insight\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "assets", "tools_schema.json"), []byte("[]"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "memory", "memory_management_sop.md"), []byte("# L0\n"), 0o644)
	if err := memory.InitMemory(root, ""); err != nil {
		t.Fatal(err)
	}
	return root
}

func toolMap(ts []agent.Tool) map[string]agent.Tool {
	m := map[string]agent.Tool{}
	for _, t := range ts {
		m[t.Name()] = t
	}
	return m
}

// TestToolResultFeedsBack 验证工具输出（file_read 内容）作为 <tool_result> 回传到下一轮 prompt。
// self-evolving 闭环的根因测试：之前 RunLoop 的 tc.ID!="" 门把所有文本协议 tool_call 的 Data 全丢，
// 模型看不到 code_run 输出 / file_read 内容。修复后 Data 无条件回传（no_tool 除外），对齐 Python agent_loop.py:95。
func TestToolResultFeedsBack(t *testing.T) {
	root := setupE2ERoot(t)
	_ = os.WriteFile(filepath.Join(root, "memory", "skill.md"), []byte("SECRET=42"), 0o644)

	mock := llm.NewMockSession([]string{
		`<tool_use>{"name":"file_read","arguments":{"path":"../memory/skill.md"}}</tool_use>`,
		"done.",
	})
	client := llm.NewToolClient(mock, "")
	toolsList := []agent.Tool{
		tools.NoTool{}, tools.FileRead{}, tools.FileWrite{}, tools.FilePatch{}, tools.CodeRun{},
		tools.UpdateWorkingCheckpoint{}, tools.StartLongTermUpdate{Root: root, Lang: ""}, tools.AskUser{},
	}
	h := loop.NewHandler(filepath.Join(root, "temp"), root, "", toolMap(toolsList))
	sink := agent.NewChanSink(128)

	go func() {
		_ = loop.RunLoop(context.Background(), sink, client, "sys", "read the skill", h, nil, 10)
		sink.Close()
	}()
	for range sink.Chan() {
	}

	if len(mock.Prompts) < 2 {
		t.Fatalf("expected >=2 prompts, got %d", len(mock.Prompts))
	}
	p2 := mock.Prompts[1]
	if !strings.Contains(p2, "<tool_result>") {
		t.Errorf("turn-2 prompt missing <tool_result> block:\n%s", p2)
	}
	if !strings.Contains(p2, "SECRET=42") {
		t.Errorf("turn-2 prompt missing file_read content (tool output not fed back — self-evolving loop blind):\n%s", p2)
	}
}

// TestCodeRunOutputFeedsBack 验证 code_run 的 stdout 作为 tool_result 回传（stringify 对 string 不加引号）。
func TestCodeRunOutputFeedsBack(t *testing.T) {
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("bash not available")
	}
	root := setupE2ERoot(t)
	mock := llm.NewMockSession([]string{
		`<tool_use>{"name":"code_run","arguments":{"type":"bash","code":"echo HELLO_99"}}</tool_use>`,
		"done.",
	})
	client := llm.NewToolClient(mock, "")
	toolsList := []agent.Tool{
		tools.NoTool{}, tools.FileRead{}, tools.FileWrite{}, tools.FilePatch{}, tools.CodeRun{},
		tools.UpdateWorkingCheckpoint{}, tools.StartLongTermUpdate{Root: root, Lang: ""}, tools.AskUser{},
	}
	h := loop.NewHandler(filepath.Join(root, "temp"), root, "", toolMap(toolsList))
	sink := agent.NewChanSink(128)

	go func() {
		_ = loop.RunLoop(context.Background(), sink, client, "sys", "run code", h, nil, 10)
		sink.Close()
	}()
	for range sink.Chan() {
	}
	if len(mock.Prompts) < 2 {
		t.Fatalf("expected >=2 prompts, got %d", len(mock.Prompts))
	}
	if !strings.Contains(mock.Prompts[1], "HELLO_99") {
		t.Errorf("code_run stdout not fed back as tool_result:\n%s", mock.Prompts[1])
	}
}
