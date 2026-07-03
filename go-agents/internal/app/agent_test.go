package app

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"genericagent/internal/agent"
	"genericagent/internal/llm"
	"genericagent/internal/memory"
	"genericagent/internal/tools"
)

func setupTempRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	for _, p := range []string{"memory", "temp", "assets"} {
		_ = os.MkdirAll(filepath.Join(root, p), 0o755)
	}
	_ = os.WriteFile(filepath.Join(root, "assets", "sys_prompt.txt"), []byte("You are an agent.\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "assets", "insight_fixed_structure.txt"), []byte("L1 structure\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "assets", "global_mem_insight_template.txt"), []byte("# insight\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "assets", "tools_schema.json"), []byte("[]"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "memory", "memory_management_sop.md"), []byte("# L0 SOP\n"), 0o644)
	if err := memory.InitMemory(root, ""); err != nil {
		t.Fatal(err)
	}
	return root
}

// TestCrossTaskKeyInfoInheritance 验证 task2 继承 task1 的 key_info + passed_sessions 递增。
// 对应 Python agentmain.py:215-220。
func TestCrossTaskKeyInfoInheritance(t *testing.T) {
	root := setupTempRoot(t)
	mock := llm.NewMockSession([]string{
		`<tool_use>{"name":"update_working_checkpoint","arguments":{"key_info":"persisted fact"}}</tool_use>`,
		"done.",
		"ok.",
	})
	client := llm.NewToolClient(mock, "")
	toolsList := []agent.Tool{
		tools.NoTool{}, tools.FileWrite{}, tools.FileRead{}, tools.FilePatch{}, tools.CodeRun{},
		tools.UpdateWorkingCheckpoint{}, tools.StartLongTermUpdate{Root: root, Lang: ""}, tools.AskUser{},
	}
	a, err := NewGenericAgent(root, "", client, toolsList)
	if err != nil {
		t.Fatal(err)
	}
	go a.Run()

	out1 := a.PutTask("task1", "test")
	for range out1 {
	}
	out2 := a.PutTask("task2", "test")
	for range out2 {
	}

	ki := a.PrevHandler.Working["key_info"]
	if !strings.Contains(ki, "persisted fact") {
		t.Fatalf("key_info not inherited: %q", ki)
	}
	if !strings.Contains(ki, "1 个对话前") {
		t.Fatalf("passed_sessions marker missing: %q", ki)
	}
	if a.PrevHandler.Working["passed_sessions"] != "1" {
		t.Fatalf("passed_sessions=%q want 1", a.PrevHandler.Working["passed_sessions"])
	}
}
