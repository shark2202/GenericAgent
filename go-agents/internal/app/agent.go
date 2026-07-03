// Package app 实现 GenericAgent 主体 + 4 种运行模式。
// 对应 Python agentmain.GenericAgent + __main__ 分发。
package app

import (
	"context"
	"fmt"
	"path/filepath"
	"regexp"
	"strconv"
	"sync"

	"genericagent/internal/agent"
	"genericagent/internal/llm"
	"genericagent/internal/loop"
	"genericagent/internal/prompt"
	"genericagent/internal/tools"
)

// GenericAgent 对应 Python GenericAgent 类。task_queue 驱动，跨任务继承 key_info。
type GenericAgent struct {
	TaskQueue   chan Task
	History     []string
	PrevHandler *loop.Handler
	Client      *llm.ToolClient
	SysPrompt   string
	Spec        []agent.ToolSpec
	Tools       map[string]agent.Tool
	Root        string
	Cwd         string
	Lang        string
	IncOut      bool
	Verbose     bool
	PeerHint    bool
	MaxTurns    int

	mu        sync.Mutex
	curCancel context.CancelFunc
	stop      bool
}

// Task 是 PutTask 的单元。Output 即 display_queue。
type Task struct {
	Query  string
	Source string
	Output chan agent.Event
}

// NewGenericAgent 初始化：sys_prompt + tools schema + 工具表。
func NewGenericAgent(root, lang string, client *llm.ToolClient, toolsList []agent.Tool) (*GenericAgent, error) {
	cwd := filepath.Join(root, "temp")
	a := &GenericAgent{
		TaskQueue: make(chan Task, 16),
		Client:    client,
		SysPrompt: prompt.GetSystemPrompt(root, lang),
		Tools:     map[string]agent.Tool{},
		Root:      root, Cwd: cwd, Lang: lang,
		MaxTurns: 180, IncOut: true, PeerHint: true,
	}
	for _, t := range toolsList {
		a.Tools[t.Name()] = t
	}
	spec, err := tools.LoadToolSchema(root, lang)
	if err != nil {
		return nil, fmt.Errorf("tool schema: %w", err)
	}
	a.Spec = spec
	return a, nil
}

// PutTask 投递任务，返回事件流（display_queue 等价）。
func (a *GenericAgent) PutTask(query, source string) chan agent.Event {
	out := make(chan agent.Event, 128)
	a.TaskQueue <- Task{Query: query, Source: source, Output: out}
	return out
}

// Run 消费 task_queue，串行执行（对应 Python run 线程）。
func (a *GenericAgent) Run() {
	for t := range a.TaskQueue {
		if a.stopped() {
			break
		}
		a.runOne(t)
	}
}

// Abort 中止当前任务并停止 Run 循环（对应 Python abort，整个 agent 不再接新任务）。
func (a *GenericAgent) Abort() {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.stop = true
	if a.curCancel != nil {
		a.curCancel()
	}
}

// AbortCurrent 仅取消当前任务，Run 循环保活（TUI 用：Ctrl-C 中止当前任务后可继续提问）。
func (a *GenericAgent) AbortCurrent() {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.curCancel != nil {
		a.curCancel()
	}
}

func (a *GenericAgent) stopped() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.stop
}

var keyInfoOldRe = regexp.MustCompile(`\n\[SYSTEM\] 此为.*?工作记忆[。\n]*`)

// runOne 执行单个任务：建 handler（继承 key_info）→ RunLoop → drain 到 Output。
func (a *GenericAgent) runOne(t Task) {
	h := loop.NewHandler(a.Cwd, a.Root, a.Lang, a.Tools)
	// 跨任务继承 key_info（agentmain.py:215-220）
	if a.PrevHandler != nil {
		if ki, ok := a.PrevHandler.Working["key_info"]; ok && ki != "" {
			ki = keyInfoOldRe.ReplaceAllString(ki, "")
			h.Working["key_info"] = ki
			ps := 0
			if v, err := strconv.Atoi(a.PrevHandler.Working["passed_sessions"]); err == nil {
				ps = v
			}
			ps++
			h.Working["passed_sessions"] = strconv.Itoa(ps)
			if ps > 0 {
				h.Working["key_info"] += fmt.Sprintf("\n[SYSTEM] 此为 %d 个对话前设置的key_info，若已在新任务，先更新或清除工作记忆。\n", ps)
			}
		}
	}

	sink := agent.NewChanSink(128)
	ctx, cancel := context.WithCancel(context.Background())
	a.mu.Lock()
	a.curCancel = cancel
	a.mu.Unlock()

	sysPrompt := a.SysPrompt
	if a.PeerHint {
		sysPrompt += "\n[Peer] 用户提及其他会话/后台任务状态时: temp/model_responses/ (只找近期修改的文件尾部)\n"
	}

	go func() {
		if err := loop.RunLoop(ctx, sink, a.Client, sysPrompt, t.Query, h, a.Spec, a.MaxTurns); err != nil {
			fmt.Printf("[RunLoop error] %v\n", err)
		}
		sink.Close()
	}()

	for ev := range sink.Chan() {
		t.Output <- ev
	}
	close(t.Output)

	a.PrevHandler = h
	a.History = h.HistoryInfo
}
