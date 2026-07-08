// Package agent 定义 core 引擎的核心类型契约（Phase 0 骨架）。
// 对应 PRD §3.3。实现见后续 Phase。
package agent

import "context"

// StepOutcome 是一次工具调用的产出，对应 Python agent_loop.StepOutcome。
type StepOutcome struct {
	Data       any
	NextPrompt string // 空 = 当前任务完成
	ShouldExit bool
}

// Event 是 core → frontend 的事件流，对应 Python display_queue 的 dict。
type Event interface{ event() }

type TurnStart struct{ Turn int }
type Chunk struct {
	Text   string
	Source string
	Turn   int
}
type ToolLog struct{ Text string }
type Done struct {
	FullResp string
	Turn     int
	Err      error
}

func (TurnStart) event() {}
func (Chunk) event()     {}
func (ToolLog) event()   {}
func (Done) event()      {}

// EventSink 是 core 对前端的输出抽象。A/B 两实现，core 只依赖此接口。
// 对应 PRD §3.4.4：A=ChanSink（同进程），B=JSONLineSink（跨进程 NDJSON，后续）。
type EventSink interface {
	Emit(Event) // 投递（A: 写 chan；B: 写 NDJSON）
	Close()     // 任务结束
}

// ChanSink —— A 阶段实现：同进程 channel，TUI 直接 range Chan()。
// ponytail: Emit 非阻塞投递，满则丢；Phase 1 带 ctx 改阻塞+取消。
type ChanSink struct{ ch chan Event }

func NewChanSink(buf int) *ChanSink {
	if buf <= 0 {
		buf = 64
	}
	return &ChanSink{ch: make(chan Event, buf)}
}

func (s *ChanSink) Emit(e Event) {
	select {
	case s.ch <- e:
	default:
	}
}

func (s *ChanSink) Close()      { close(s.ch) }
func (s *ChanSink) Chan() <-chan Event { return s.ch }

// Message 对应 Python messages 元素。
type Message struct {
	Role       string
	Content    string
	ToolResults []ToolResult `json:"tool_results,omitempty"`
}

type ToolResult struct {
	ToolUseID string `json:"tool_use_id"`
	Content   string `json:"content"`
}

type ToolCall struct {
	Name      string
	Arguments map[string]any
	ID        string
}

// Response 统一 LLM 返回，对应 Python MockResponse。
type Response struct {
	Content    string
	ToolCalls  []ToolCall
	StopReason string
}

// ToolSpec 是 tools_schema.json 的一项。
type ToolSpec struct {
	Function ToolFunc `json:"function"`
}
type ToolFunc struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Parameters  map[string]any `json:"parameters"`
}

// Stream 替代 Python generator。
type Stream[T any] <-chan T

// ToolEvent 是工具 dispatch 流的事件：Log=中间日志，Outcome=最终产出（nil 表示还没结束）。
// 对应 Python do_<name> generator 的 yield（日志）+ return（StepOutcome）。
type ToolEvent struct {
	Log     string
	Outcome *StepOutcome
}

// Tool 接口：对应 Python do_<name> generator。
// Dispatch 发若干 Log 事件，最后一个带 Outcome。
type Tool interface {
	Name() string
	Dispatch(ctx context.Context, args map[string]any, resp Response) Stream[ToolEvent]
}

// Session 是 LLM 后端接口，对应 Python BaseSession。
type Session interface {
	Ask(ctx context.Context, prompt string) Stream[string]
	History() []Message
	SetHistory([]Message)
}
