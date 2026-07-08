// Package llm 实现 ToolClient（协议降级）+ 各 Session 后端。
// Phase 1：ToolClient + MockSession。OAI 真流式待 mykey 配置后验证。
package llm

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"

	"genericagent/internal/agent"
)

// ToolClient 包装 Session，提供协议降级 chat。
// 对应 Python llmcore.ToolClient。
type ToolClient struct {
	Backend     agent.Session
	LastTools   string
	AutoSaveTok bool
	LogPath     string
	totalCdTok  int
	lang        string // "en" 或 ""
}

func NewToolClient(b agent.Session, lang string) *ToolClient {
	return &ToolClient{Backend: b, AutoSaveTok: true, lang: lang}
}

// ChatItem 是 chat 流的事件：Chunk=流式文本，Final=解析后的 Response（末尾一发）。
type ChatItem struct {
	Chunk string
	Final *agent.Response
}

// Chat 构建 protocol prompt → 调 Backend.Ask 流式 → 末尾解析 Response。
func (c *ToolClient) Chat(ctx context.Context, msgs []agent.Message, tools []agent.ToolSpec) <-chan ChatItem {
	ch := make(chan ChatItem, 64)
	prompt := c.BuildProtocolPrompt(msgs, tools)
	go func() {
		defer close(ch)
		stream := c.Backend.Ask(ctx, prompt)
		var b strings.Builder
		for s := range stream {
			b.WriteString(s)
			ch <- ChatItem{Chunk: s}
		}
		resp := ParseMixedResponse(b.String())
		ch <- ChatItem{Final: &resp}
	}()
	return ch
}

// BuildProtocolPrompt 对应 Python ToolClient._build_protocol_prompt + _prepare_tool_instruction。
// 把 system + tool 指令 + history（USER/ASSISTANT 块 + tool_result）拍平为单 prompt。
func (c *ToolClient) BuildProtocolPrompt(msgs []agent.Message, tools []agent.ToolSpec) string {
	var sysContent string
	var hist []agent.Message
	for _, m := range msgs {
		if strings.EqualFold(m.Role, "system") {
			sysContent = m.Content
		} else {
			hist = append(hist, m)
		}
	}
	toolInstr := c.prepareToolInstruction(tools)

	var sys, user strings.Builder
	if sysContent != "" {
		sys.WriteString(sysContent + "\n")
	}
	sys.WriteString(toolInstr)
	for _, m := range hist {
		role := "USER"
		if !strings.EqualFold(m.Role, "user") {
			role = "ASSISTANT"
		}
		user.WriteString("=== " + role + " ===\n")
		for _, tr := range m.ToolResults {
			user.WriteString("<tool_result>" + tr.Content + "</tool_result>\n")
		}
		user.WriteString(m.Content + "\n")
		c.totalCdTok += len(user.String()) / 3
	}
	if c.totalCdTok > 9000 {
		c.LastTools = ""
	}
	user.WriteString("=== ASSISTANT ===\n")
	return sys.String() + user.String()
}

// prepareToolInstruction 对应 _prepare_tool_instruction：tools JSON + 协议说明 + last_tools 缓存。
func (c *ToolClient) prepareToolInstruction(tools []agent.ToolSpec) string {
	if len(tools) == 0 {
		return ""
	}
	toolsJSON, _ := json.Marshal(tools)
	tj := string(toolsJSON)
	var instr string
	if c.lang == "en" {
		instr = `
### Interaction Protocol (must follow strictly, always in effect)
Follow these steps to think and act:
1. **Think**: Analyze the current situation and strategy inside <thinking> tags.
2. **Summarize**: Output a minimal one-line (<30 words) physical snapshot in <summary>: new info from last tool result + current tool call intent. This goes into long-term working memory. Must contain real information, no filler.
3. **Act**: If you need to call tools, output one or more **<tool_use> blocks** after your reply, then stop.
`
	} else {
		instr = `
### 交互协议 (必须严格遵守，持续有效)
请按照以下步骤思考并行动：
1. **思考**: 在 <thinking> 标签中先进行思考，分析现状和策略。
2. **总结**: 在 <summary> 中输出*极为简短*的高度概括的单行（<30字）物理快照，包括上次工具调用结果产生的新信息+本次工具调用意图。此内容将进入长期工作记忆，记录关键信息，严禁输出无实际信息增量的描述。
3. **行动**: 如需调用工具，请在回复正文之后输出一个（或多个）**<tool_use>块**，然后结束。
`
	}
	example := "\n示例（必须照此格式输出）:\n<thinking>需计算2**10</thinking>\n<summary>用code_run执行python计算</summary>\n<tool_use>{\"name\":\"code_run\",\"arguments\":{\"type\":\"python\",\"script\":\"print(2**10)\"}}</tool_use>\n"
	if c.lang == "en" {
		example = "\nExample (must follow this format):\n<thinking>need to compute 2**10</thinking>\n<summary>run code_run to compute</summary>\n<tool_use>{\"name\":\"code_run\",\"arguments\":{\"type\":\"python\",\"script\":\"print(2**10)\"}}</tool_use>\n"
	}
	instr += "\nFormat: ```<tool_use>{\"name\": \"tool_name\", \"arguments\": {...}}</tool_use>```" + example + "\n### Tools (mounted, always in effect):\n" + tj + "\n"
	if c.AutoSaveTok && c.LastTools == tj {
		if c.lang == "en" {
			instr = "\n### Tools: still active, **ready to call**. Protocol unchanged.\n"
		} else {
			instr = "\n### 工具库状态：持续有效（code_run/file_read等），**可正常调用**。调用协议沿用。\n"
		}
	} else {
		c.totalCdTok = 0
	}
	c.LastTools = tj
	return instr
}

// ParseMixedResponse 对应 _parse_mixed_response：解析 <thinking> + <tool_use> 文本块。
func ParseMixedResponse(text string) agent.Response {
	remaining := text
	// <thinking>...</thinking>
	thinkingRe := regexp.MustCompile(`(?s)<think(?:ing)?>(.*?)</think(?:ing)?>`)
	if m := thinkingRe.FindStringSubmatch(remaining); m != nil {
		remaining = thinkingRe.ReplaceAllString(remaining, "")
	}
	tcs, remaining := parseTextToolCalls(remaining)
	// ponytail: fallback 裸 JSON 暂不实现，<tool_use> 标签足够 Mock + 主流模型
	return agent.Response{
		Content:   strings.TrimSpace(remaining),
		ToolCalls: tcs,
	}
}

// parseTextToolCalls 对应 _parse_text_tool_calls：提取 <tool_use>{json}</tool_use>。
// ponytail: RE2 不支持负前瞻，用非贪婪替代；嵌套边缘 case 罕见，先不管。
var toolUseRe = regexp.MustCompile(`(?s)<(?:tool_use|tool_call)>(.*?)</(?:tool_use|tool_call)>`)

func parseTextToolCalls(content string) ([]agent.ToolCall, string) {
	var tcs []agent.ToolCall
	matches := toolUseRe.FindAllStringSubmatch(content, -1)
	for _, m := range matches {
		s := strings.TrimSpace(m[1])
		d, err := parseToolJSON(s)
		if err != nil || d == nil {
			continue
		}
		name, _ := d["name"].(string)
		args := map[string]any{}
		if a, ok := d["arguments"].(map[string]any); ok {
			args = a
		} else if a, ok := d["args"].(map[string]any); ok {
			args = a
		}
		if name != "" {
			tcs = append(tcs, agent.ToolCall{Name: name, Arguments: args})
		}
	}
	if len(tcs) > 0 {
		content = toolUseRe.ReplaceAllString(content, "")
	}
	return tcs, strings.TrimSpace(content)
}

// parseToolJSON 容错 JSON 解析（对应 Python tryparse）。
func parseToolJSON(s string) (map[string]any, error) {
	s = strings.TrimSpace(s)
	s = strings.Trim(s, "`")
	s = strings.TrimPrefix(s, "json\n")
	s = strings.TrimSpace(s)
	var d map[string]any
	if err := json.Unmarshal([]byte(s), &d); err == nil {
		return d, nil
	}
	// fallback: 提取第一个 {...}
	i := strings.Index(s, "{")
	j := strings.LastIndex(s, "}")
	if i >= 0 && j > i {
		if err := json.Unmarshal([]byte(s[i:j+1]), &d); err == nil {
			return d, nil
		}
	}
	return nil, fmt.Errorf("bad tool json")
}

// envLang 读取 GA_LANG。
func envLang() string { return os.Getenv("GA_LANG") }
