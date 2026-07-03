package loop

import (
	"path/filepath"
	"strings"
	"testing"

	"genericagent/internal/agent"
	"genericagent/internal/memory"
)

func TestTurnEndPeriodicInjection(t *testing.T) {
	abs, _ := filepath.Abs("../..") // -> GenericAgent
	h := NewHandler(".", abs, "", map[string]agent.Tool{})
	cases := []struct {
		turn     int
		contains string
		label    string
	}{
		{7, "update_working_checkpoint", "turn%7"},
		{25, "**file**", "turn%25"},
		{175, "ask_user", "turn%175"},
	}
	for _, c := range cases {
		resp := agent.Response{Content: "<summary>test</summary>"}
		out := h.TurnEnd(resp, []agent.ToolCall{{Name: "no_tool"}}, nil, c.turn, "")
		if !strings.Contains(out, c.contains) {
			t.Errorf("%s turn=%d: expected %q in:\n%s", c.label, c.turn, c.contains, out)
		}
	}
}

func TestTurnEndTurn10InjectsMemory(t *testing.T) {
	abs, _ := filepath.Abs("../../..") // -> GenericAgent (from internal/loop)
	if err := memory.InitMemory(abs, ""); err != nil {
		t.Skipf("InitMemory: %v", err)
	}
	h := NewHandler(".", abs, "", map[string]agent.Tool{})
	resp := agent.Response{Content: "<summary>t</summary>"}
	out := h.TurnEnd(resp, []agent.ToolCall{{Name: "no_tool"}}, nil, 10, "")
	if !strings.Contains(out, "[Memory]") {
		t.Errorf("turn 10 should inject global memory:\n%s", out)
	}
}

func TestTurnEndSummaryFallback(t *testing.T) {
	abs, _ := filepath.Abs("../..")
	h := NewHandler(".", abs, "", map[string]agent.Tool{})
	resp := agent.Response{Content: "no summary here"}
	out := h.TurnEnd(resp, []agent.ToolCall{{Name: "file_write", Arguments: map[string]any{"path": "x"}}}, nil, 1, "")
	if !strings.Contains(out, "必须在回复文本中包含<summary>") {
		t.Errorf("missing summary reminder:\n%s", out)
	}
	if len(h.HistoryInfo) == 0 || !strings.HasPrefix(h.HistoryInfo[0], "[Agent] ") {
		t.Errorf("history not appended: %v", h.HistoryInfo)
	}
}

func TestGetAnchorPromptKeyInfo(t *testing.T) {
	h := NewHandler(".", ".", "", map[string]agent.Tool{})
	h.SetKeyInfo("关键端口8080")
	h.SetRelatedSop("memory_management_sop")
	h.CurrentTurn = 5
	h.HistoryInfo = []string{"[USER] hi", "[Agent] did x"}
	p := h.GetAnchorPrompt(false)
	if !strings.Contains(p, "关键端口8080") {
		t.Errorf("missing key_info:\n%s", p)
	}
	if !strings.Contains(p, "Current turn: 5") {
		t.Errorf("missing turn")
	}
	if !strings.Contains(p, "memory_management_sop") {
		t.Errorf("missing related_sop")
	}
	if h.GetAnchorPrompt(true) != "\n" {
		t.Errorf("skip should return newline, got %q", h.GetAnchorPrompt(true))
	}
}

func TestFoldEarlier(t *testing.T) {
	h := NewHandler(".", ".", "", map[string]agent.Tool{})
	lines := []string{
		"[USER] task1",
		"[Agent] step a",
		"[Agent] step b",
		"[USER] task2",
		"[Agent] 直接回答了用户问题",
		"[Agent] 直接回答了用户问题",
	}
	out := h.FoldEarlier(lines)
	if !strings.Contains(out, "[USER] task1") {
		t.Errorf("user line lost: %s", out)
	}
	if !strings.Contains(out, "step b（2 turns）") {
		t.Errorf("agent fold lost (expect last='step b'): %s", out)
	}
	if !strings.Contains(out, "[Agent]（2 turns）") {
		t.Errorf("fallback fold lost: %s", out)
	}
}
