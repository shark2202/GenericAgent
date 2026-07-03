// Package loop 实现 agent 主循环（RunLoop）+ Handler。
// 对应 Python agent_loop.agent_runner_loop + ga.GenericAgentHandler。
package loop

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"genericagent/internal/agent"
	"genericagent/internal/llm"
	"genericagent/internal/memory"
)

// Handler 对应 GenericAgentHandler。
// Phase 2：完整周期注入 + working memory + anchor prompt。
type Handler struct {
	Working     map[string]string // key_info/related_sop/passed_sessions
	HistoryInfo []string          // 对应 history_info（summary 流）
	History     []string          // 旧字段保留
	Cwd         string
	Root        string
	Lang        string
	Tools       map[string]agent.Tool
	CurrentTurn int
	Verbose     bool
	// Phase 3 待补：Parent（extrakeyinfo/intervene/task_dir）、plan 模式
}

func NewHandler(cwd, root, lang string, tools map[string]agent.Tool) *Handler {
	return &Handler{
		Working: map[string]string{}, History: []string{}, Cwd: cwd,
		Root: root, Lang: lang, Tools: tools,
	}
}

// SetKeyInfo/SetRelatedSop 供 tools 包工具调用。
func (h *Handler) SetKeyInfo(s string)       { h.Working["key_info"] = s }
func (h *Handler) SetRelatedSop(s string)    { h.Working["related_sop"] = s }

// Dispatch 对应 BaseHandler.dispatch：按名找工具，注入 _cwd/_index/_tool_num。
func (h *Handler) Dispatch(ctx context.Context, name string, args map[string]any, resp agent.Response, index, toolNum int) agent.Stream[agent.ToolEvent] {
	if args == nil {
		args = map[string]any{}
	}
	args["_cwd"] = h.Cwd
	args["_root"] = h.Root
	args["_handler"] = h
	args["_index"] = index
	args["_tool_num"] = toolNum
	t, ok := h.Tools[name]
	if !ok {
		ch := make(chan agent.ToolEvent, 1)
		go func() {
			defer close(ch)
			ch <- agent.ToolEvent{Log: fmt.Sprintf("未知工具: %s\n", name), Outcome: &agent.StepOutcome{NextPrompt: "未知工具 " + name}}
		}()
		return ch
	}
	return t.Dispatch(ctx, args, resp)
}

var (
	summaryRe    = regexp.MustCompile(`(?s)<summary>(.*?)</summary>`)
	codeBlockRe  = regexp.MustCompile("(?s)```.*?```")
	thinkStripRe = regexp.MustCompile("(?s)<thinking>.*?</thinking>")
)

// TurnEnd 对应 turn_end_callback：summary 提取 + 周期注入（turn%7/10/25/175）。
// plan 模式与 task 文件注入（consume_file _keyinfo/_intervene）留 Phase 3。
func (h *Handler) TurnEnd(resp agent.Response, toolCalls []agent.ToolCall, toolResults []agent.ToolResult, turn int, nextPrompt string) string {
	cleaned := stripCodeAndThinking(resp.Content)
	summary := ""
	if m := summaryRe.FindStringSubmatch(cleaned); m != nil {
		summary = strings.TrimSpace(m[1])
	} else {
		tc := toolCalls[0]
		cleanArgs := map[string]any{}
		for k, v := range tc.Arguments {
			if !strings.HasPrefix(k, "_") {
				cleanArgs[k] = v
			}
		}
		if strings.TrimSpace(cleaned) == "" {
			if tc.Name == "no_tool" {
				summary = "直接回答了用户问题"
			} else {
				summary = smartFormat(fmt.Sprintf("%s, args: %v", tc.Name, cleanArgs), 40)
			}
		} else {
			summary = smartFormat(cleaned, 40)
		}
		nextPrompt += "\n\n\n[SYSTEM] 必须在回复文本中包含<summary>！\n\n"
	}
	summary = smartFormat(strings.ReplaceAll(summary, "\n", ""), 80)
	h.HistoryInfo = append(h.HistoryInfo, "[Agent] "+summary)

	// 周期注入
	if turn%175 == 0 {
		nextPrompt += fmt.Sprintf("\n\n[DANGER] Turn %d. Must call ask_user to summarize progress and get direction. No more blind retries.", turn)
	} else if turn%7 == 0 {
		nextPrompt += fmt.Sprintf("\n\n[SYSTEM] Turn %d. Call update_working_checkpoint to save key context. Stop ineffective retries; if no progress, switch strategy: 1) Probe physical boundaries 2) **Re-read relevant SOPs**", turn)
	} else if turn%25 == 0 {
		nextPrompt += fmt.Sprintf("\n\n[SYSTEM] Turn %d. Write checkpoints/key findings/tried approaches to a **file** for future reference (not only working_checkpoint!). Avoid losing critical info.", turn)
	} else if turn%10 == 0 {
		nextPrompt += memory.GetGlobalMemory(h.Root, h.Lang)
	}
	return nextPrompt
}

// GetAnchorPrompt 对应 _get_anchor_prompt：working memory 注入（history 折叠 + key_info + related_sop）。
func (h *Handler) GetAnchorPrompt(skip bool) string {
	if skip {
		return "\n"
	}
	const W = 30
	var earlier string
	if len(h.HistoryInfo) > W {
		earlier = "<earlier_context>\n" + h.FoldEarlier(h.HistoryInfo[:len(h.HistoryInfo)-W]) + "\n</earlier_context>\n"
	}
	end := len(h.HistoryInfo)
	if end > W {
		end = W
	}
	hStr := strings.Join(h.HistoryInfo[len(h.HistoryInfo)-end:], "\n")
	p := "\n### [WORKING MEMORY]\n" + earlier + "<history>\n" + hStr + "\n</history>"
	p += fmt.Sprintf("\nCurrent turn: %d\n", h.CurrentTurn)
	if ki := h.Working["key_info"]; ki != "" {
		p += "\n<key_info>" + ki + "</key_info>"
	}
	if rs := h.Working["related_sop"]; rs != "" {
		p += fmt.Sprintf("\n有不清晰的地方请再次读取%s", rs)
	}
	if h.Verbose {
		fmt.Fprintln(logWriter, p)
	}
	return p
}

// FoldEarlier 对应 _fold_earlier：合并连续 [Agent] 行为 "summary（N turns）"。
func (h *Handler) FoldEarlier(lines []string) string {
	fallback := "直接回答了用户问题"
	var parts []string
	cnt := 0
	last := ""
	flush := func() {
		if cnt > 0 {
			if strings.Contains(last, fallback) {
				parts = append(parts, fmt.Sprintf("[Agent]（%d turns）", cnt))
			} else {
				parts = append(parts, fmt.Sprintf("%s（%d turns）", last, cnt))
			}
		}
	}
	for _, line := range lines {
		if strings.HasPrefix(line, "[USER]") {
			flush()
			parts = append(parts, line)
			cnt = 0
			last = ""
		} else {
			cnt++
			last = line
		}
	}
	flush()
	if len(parts) > 70 {
		parts = parts[len(parts)-70:]
	}
	return strings.Join(parts, "\n")
}

// RunLoop 对应 agent_runner_loop。chan Event 驱动，ctx 中止。
func RunLoop(ctx context.Context, sink agent.EventSink, client *llm.ToolClient, sysPrompt, user string, h *Handler, tools []agent.ToolSpec, maxTurns int) error {
	messages := []agent.Message{
		{Role: "system", Content: sysPrompt},
		{Role: "user", Content: user},
	}
	h.HistoryInfo = append(h.HistoryInfo, "[USER] "+user)
	var fullResp strings.Builder
	for turn := 1; turn <= maxTurns; turn++ {
		h.CurrentTurn = turn
		// agent_loop.py:56 每 10 轮重置工具描述缓存
		if turn%10 == 0 {
			client.LastTools = ""
		}
		sink.Emit(agent.TurnStart{Turn: turn})
		sink.Emit(agent.Chunk{Text: fmt.Sprintf("\n\nTurn %d ...\n\n", turn)})
		fullResp.WriteString(fmt.Sprintf("\n\nTurn %d ...\n\n", turn))

		ch := client.Chat(ctx, messages, tools)
		var resp agent.Response
		for item := range ch {
			if item.Final != nil {
				resp = *item.Final
				continue
			}
			sink.Emit(agent.Chunk{Text: item.Chunk})
			fullResp.WriteString(item.Chunk)
		}

		var toolCalls []agent.ToolCall
		if len(resp.ToolCalls) == 0 {
			toolCalls = []agent.ToolCall{{Name: "no_tool"}}
		} else {
			toolCalls = resp.ToolCalls
		}

		var toolResults []agent.ToolResult
		nextPrompts := map[string]bool{}
		exited := false
		for i, tc := range toolCalls {
			sink.Emit(agent.ToolLog{Text: fmt.Sprintf("🛠️ %s\n", tc.Name)})
			args := tc.Arguments
			evCh := h.Dispatch(ctx, tc.Name, args, resp, i, len(toolCalls))
			var outcome *agent.StepOutcome
			for ev := range evCh {
				if ev.Log != "" {
					sink.Emit(agent.ToolLog{Text: ev.Log})
				}
				if ev.Outcome != nil {
					outcome = ev.Outcome
				}
			}
			if outcome == nil {
				outcome = &agent.StepOutcome{}
			}
			if outcome.ShouldExit {
				exited = true
				break
			}
			if outcome.NextPrompt == "" {
				break
			}
			// 对齐 Python agent_loop.py:95：文本协议 tool_call 无 id 也回传 Data（no_tool 除外）。
			// 之前 tc.ID!="" 门把所有 Mock/文本协议的 Data 全丢了——模型看不到 code_run 输出/file_read 内容。
			if outcome.Data != nil && tc.Name != "no_tool" {
				toolResults = append(toolResults, agent.ToolResult{ToolUseID: tc.ID, Content: stringify(outcome.Data)})
			}
			nextPrompts[outcome.NextPrompt] = true
		}
		if exited || len(nextPrompts) == 0 {
			break
		}
		nextPrompt := h.TurnEnd(resp, toolCalls, toolResults, turn, joinKeys(nextPrompts, "\n"))
		messages = []agent.Message{{Role: "user", Content: nextPrompt, ToolResults: toolResults}}
		h.HistoryInfo = append(h.HistoryInfo, "[USER] "+nextPrompt)
	}
	sink.Emit(agent.Done{FullResp: fullResp.String()})
	return nil
}

func stripCodeAndThinking(s string) string {
	s = codeBlockRe.ReplaceAllString(s, "")
	s = thinkStripRe.ReplaceAllString(s, "")
	return s
}

func smartFormat(s string, maxLen int) string {
	s = strings.TrimSpace(s)
	// ponytail: Python smart_format 还压长字符串中间，这里简化为尾部截断
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen] + "..."
}

var logWriter = newDefaultWriter()

func joinKeys(m map[string]bool, sep string) string {
	var keys []string
	for k := range m {
		keys = append(keys, k)
	}
	return strings.Join(keys, sep)
}

// stringify 对齐 Python agent_loop datastr：dict/list→JSON（不转义 HTML，匹配 ensure_ascii=False），
// string→原样（Python str() 不加引号），其余→%v。
func stringify(v any) string {
	switch v.(type) {
	case string:
		return v.(string)
	case map[string]any, []any:
		var b bytes.Buffer
		enc := json.NewEncoder(&b)
		enc.SetEscapeHTML(false)
		if err := enc.Encode(v); err != nil {
			return fmt.Sprintf("%v", v)
		}
		return strings.TrimSpace(b.String())
	default:
		b, err := json.Marshal(v)
		if err != nil {
			return fmt.Sprintf("%v", v)
		}
		return string(b)
	}
}
