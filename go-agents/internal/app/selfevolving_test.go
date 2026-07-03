package app

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"genericagent/internal/agent"
	"genericagent/internal/llm"
	"genericagent/internal/tools"
)

// TestSelfEvolvingT5CrossRun 验证 PRD §7.4-2 / §7.3-T5：
// run1 生成脚本→code_run 验证→存 L3→run2 file_read 引用→code_run 执行成功。
// 跨任务技能复用 = self-evolving 闭环。bash 缺失则 skip。
func TestSelfEvolvingT5CrossRun(t *testing.T) {
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("bash not available")
	}
	root := setupTempRoot(t)
	skillPath := filepath.Join(root, "memory", "skill_pow2.sh")

	// 单 Mock 序列跨两任务：task1 消费 [0-2]，task2 消费 [3-5]。
	mock := llm.NewMockSession([]string{
		// task1: verify → store L3 → done
		`<tool_use>{"name":"code_run","arguments":{"type":"bash","code":"echo $((2**10))"}}</tool_use>`,
		`<tool_use>{"name":"file_write","arguments":{"path":"../memory/skill_pow2.sh","content":"echo $((2**10))"}}</tool_use>`,
		"task1 done.",
		// task2: read L3 → reuse skill → done
		`<tool_use>{"name":"file_read","arguments":{"path":"../memory/skill_pow2.sh"}}</tool_use>`,
		`<tool_use>{"name":"code_run","arguments":{"type":"bash","code":"echo $((2**10))"}}</tool_use>`,
		"task2 done.",
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

	// ---- task1: 生成并存储技能 ----
	for range a.PutTask("compute 2^10 and store the skill", "test") {
	}
	content, err := os.ReadFile(skillPath)
	if err != nil {
		t.Fatalf("L3 skill not persisted: %v", err)
	}
	if !strings.Contains(string(content), "echo $((2**10))") {
		t.Fatalf("L3 skill content wrong: %q", content)
	}

	// ---- task2: 复用存储的技能 ----
	for range a.PutTask("reuse the stored skill to compute 2^10", "test") {
	}

	// tool_result 回传验证（依赖 RunLoop 的 Data 回传修复）：
	//   Prompts 索引 0-2=task1, 3-5=task2。
	//   Prompts[4]（task2 turn2）应含 file_read 读回的技能内容（run1 写的 L3 被 run2 读到）。
	//   Prompts[5]（task2 turn3）应含 code_run 输出 1024（读回的技能执行成功）。
	if len(mock.Prompts) < 6 {
		t.Fatalf("expected >=6 prompts, got %d", len(mock.Prompts))
	}
	if !strings.Contains(mock.Prompts[4], "echo $((2**10))") {
		t.Errorf("task2 turn2: skill content not read back from L3 (file_read result missing):\n%s", mock.Prompts[4])
	}
	if !strings.Contains(mock.Prompts[5], "1024") {
		t.Errorf("task2 turn3: code_run output (skill reuse) not fed back:\n%s", mock.Prompts[5])
	}
}
