package llm

import (
	"context"

	"genericagent/internal/agent"
)

// MockSession 返回预设的响应序列，用于 Phase 1 L1 单元验收（无真 LLM key 时驱动 RunLoop）。
// 每个 Ask 弹出一个预设响应；耗尽则返回空（结束循环）。Prompts 记录每次收到的 prompt，供测试断言 tool_result 回传。
type MockSession struct {
	Responses []string // 预设响应序列
	Prompts   []string // 记录每次 Ask 收到的 prompt
	idx       int
	history   []agent.Message
}

func NewMockSession(responses []string) *MockSession {
	return &MockSession{Responses: responses}
}

func (m *MockSession) Ask(ctx context.Context, prompt string) agent.Stream[string] {
	m.Prompts = append(m.Prompts, prompt)
	ch := make(chan string, 1)
	go func() {
		defer close(ch)
		if m.idx < len(m.Responses) {
			ch <- m.Responses[m.idx]
			m.idx++
		}
	}()
	return ch
}

func (m *MockSession) History() []agent.Message        { return m.history }
func (m *MockSession) SetHistory(h []agent.Message)    { m.history = h }
func (m *MockSession) Name() string                    { return "mock" }
