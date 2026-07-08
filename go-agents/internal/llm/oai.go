package llm

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"genericagent/internal/agent"
)

// OaiSession 对应 Python llmcore.NativeOAISession（chat_completions 流式）。
// 纯 Go net/http + SSE 解析。复刻 _openai_stream（chat_completions 分支）+ _stream_with_retry。
//
// 文本协议：prompt 已含 system+工具指令+history（ToolClient.BuildProtocolPrompt 拍平），
// 故 payload 不带 tools 数组，模型在 content 文本里输出 <tool_use> 块，由 ToolClient 解析。
//
// ponytail: 仅 chat_completions；responses API / proxy / verify / read_timeout 流式强制未实现，
// add when 需对接 /v1/responses 端点或代理/卡死保护。原生 tool_calls（delta.tool_calls）忽略
// ——文本协议不用原生函数调用；如模型强制原生，add when 实测遇到再转 <tool_use> 文本。
type OaiSession struct {
	name            string
	apiKey          string
	apiBase         string
	model           string
	temperature     float64
	maxTokens       *int
	reasoningEffort string
	maxRetries      int
	userAgent       string
	history         []agent.Message
	client          *http.Client
}

// NewOaiSessionFromCfg 从 mykey cfg map 构造。对应 BaseSession.__init__ + NativeOAISession 字段。
func NewOaiSessionFromCfg(cfg map[string]any) (*OaiSession, error) {
	apiKey := strval(cfg, "apikey")
	if apiKey == "" {
		if env := strval(cfg, "apikey_env"); env != "" {
			apiKey = os.Getenv(env)
		}
	}
	apiBase := strings.TrimRight(strval(cfg, "apibase"), "/")
	model := strval(cfg, "model")
	if apiKey == "" || apiBase == "" || model == "" {
		return nil, fmt.Errorf("oai cfg missing apikey/apibase/model")
	}
	s := &OaiSession{
		apiKey:      apiKey,
		apiBase:     apiBase,
		model:       model,
		name:        strval(cfg, "name"),
		temperature: 1.0,
		maxRetries:  4, // 对应 BaseSession 默认 cfg.get('max_retries', 4)
		userAgent:   strval(cfg, "user_agent"),
		client:      &http.Client{},
	}
	if s.name == "" {
		s.name = model
	}
	if v, ok := f64val(cfg, "temperature"); ok {
		s.temperature = v
	}
	if v := strval(cfg, "reasoning_effort"); v != "" {
		s.reasoningEffort = v
	}
	if n, ok := f64val(cfg, "max_retries"); ok && n > 0 {
		s.maxRetries = int(n)
	}
	if n, ok := f64val(cfg, "max_tokens"); ok && n > 0 {
		ni := int(n)
		s.maxTokens = &ni
	}
	if s.maxTokens == nil {
		// 对应 Python：if self.max_tokens is None: self.max_tokens = 8192
		ni := 8192
		s.maxTokens = &ni
	}
	return s, nil
}

func (s *OaiSession) Name() string                 { return s.name }
func (s *OaiSession) History() []agent.Message     { return s.history }
func (s *OaiSession) SetHistory(h []agent.Message) { s.history = h }

func (s *OaiSession) ua() string {
	if s.userAgent != "" {
		return s.userAgent
	}
	return "codex_exec/0.139.0 (external, cli)"
}

// Ask 对应 BaseSession.ask + _openai_stream：发 prompt → 流式 SSE → yield 文本块。
func (s *OaiSession) Ask(ctx context.Context, prompt string) agent.Stream[string] {
	ch := make(chan string, 64)
	go func() {
		defer close(ch)
		s.askStream(ctx, prompt, ch)
	}()
	return ch
}

var versionRe = regexp.MustCompile(`/v\d+(/|$)`)

// autoMakeURL 对应 auto_make_url。
func autoMakeURL(base, path string) string {
	b := strings.TrimRight(base, "/")
	p := strings.Trim(path, "/")
	if strings.HasSuffix(b, "$") {
		return strings.TrimRight(strings.TrimSuffix(b, "$"), "/")
	}
	if p == "" || strings.HasSuffix(b, "/"+p) {
		return b
	}
	if versionRe.MatchString(b + "/") {
		return b + "/" + p
	}
	return b + "/v1/" + p
}

func (s *OaiSession) buildPayload(prompt string) (map[string]any, error) {
	payload := map[string]any{
		"model":          s.model,
		"messages":       []map[string]any{{"role": "user", "content": prompt}},
		"stream":         true,
		"stream_options": map[string]any{"include_usage": true},
	}
	if s.temperature != 1 {
		payload["temperature"] = s.temperature
	}
	if s.maxTokens != nil {
		key := "max_tokens"
		ml := strings.ToLower(s.model)
		if strings.HasPrefix(ml, "gpt-5") || strings.HasPrefix(ml, "o1") || strings.HasPrefix(ml, "o2") || strings.HasPrefix(ml, "o3") || strings.HasPrefix(ml, "o4") {
			key = "max_completion_tokens"
		}
		payload[key] = *s.maxTokens
	}
	if s.reasoningEffort != "" {
		payload["reasoning_effort"] = s.reasoningEffort
	}
	return payload, nil
}

var retryableStatus = map[int]bool{
	408: true, 409: true, 425: true, 429: true,
	500: true, 502: true, 503: true, 504: true,
	520: true, 521: true, 522: true, 523: true, 524: true, 525: true, 526: true, 527: true, 529: true,
}

func (s *OaiSession) askStream(ctx context.Context, prompt string, ch chan<- string) {
	payload, err := s.buildPayload(prompt)
	if err != nil {
		ch <- "!!!Error: " + err.Error()
		return
	}
	body, _ := json.Marshal(payload)
	url := autoMakeURL(s.apiBase, "chat/completions")
	var content strings.Builder
	for attempt := 0; attempt <= s.maxRetries; attempt++ {
		req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
		if err != nil {
			ch <- "!!!Error: " + err.Error()
			return
		}
		req.Header.Set("Authorization", "Bearer "+s.apiKey)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "text/event-stream")
		req.Header.Set("User-Agent", s.ua())
		resp, err := s.client.Do(req)
		if err != nil {
			if attempt < s.maxRetries {
				s.backoff(ctx, nil, attempt)
				continue
			}
			ch <- "!!!Error: " + err.Error()
			return
		}
		if resp.StatusCode >= 400 {
			errBody, _ := io.ReadAll(io.LimitReader(resp.Body, 500))
			resp.Body.Close()
			if retryableStatus[resp.StatusCode] && attempt < s.maxRetries {
				s.backoff(ctx, resp, attempt)
				continue
			}
			e := fmt.Sprintf("!!!Error: HTTP %d", resp.StatusCode)
			if len(errBody) > 0 {
				e += ": " + strings.TrimSpace(string(errBody))
			}
			ch <- e
			return
		}
		// 流式 SSE：只对「连接/HTTP 错误」重试；一旦开始吐字则不重试（避免重复 yield）。
		parseErr := s.parseSSE(resp.Body, ch, &content)
		resp.Body.Close()
		if parseErr != nil {
			ch <- "!!!Error: " + parseErr.Error()
		}
		if c := content.String(); c != "" && !strings.HasPrefix(strings.TrimSpace(c), "!!!Error:") {
			s.history = append(s.history, agent.Message{Role: "assistant", Content: c})
		}
		return
	}
}

// parseSSE 对应 _parse_openai_sse chat_completions 分支：逐行 data: {json}，yield delta.content。
// reasoning_content/reasoning 也 yield（匹配 Python），其余（usage 帧/error）按需处理。
func (s *OaiSession) parseSSE(body io.Reader, ch chan<- string, content *strings.Builder) error {
	sc := bufio.NewScanner(body)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Text()
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		data := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if data == "[DONE]" {
			return nil
		}
		var evt map[string]any
		if err := json.Unmarshal([]byte(data), &evt); err != nil {
			continue
		}
		if e, ok := evt["error"].(map[string]any); ok {
			if msg, ok := e["message"].(string); ok && msg != "" {
				return fmt.Errorf("api: %s", msg)
			}
			return fmt.Errorf("api: %v", e)
		}
		choices, _ := evt["choices"].([]any)
		if len(choices) == 0 {
			continue
		}
		choice, _ := choices[0].(map[string]any)
		delta, _ := choice["delta"].(map[string]any)
		if delta == nil {
			continue
		}
		if rc, ok := delta["reasoning_content"].(string); ok && rc != "" {
			content.WriteString(rc)
			ch <- rc
		} else if r, ok := delta["reasoning"].(string); ok && r != "" {
			content.WriteString(r)
			ch <- r
		}
		if t, ok := delta["content"].(string); ok && t != "" {
			content.WriteString(t)
			ch <- t
		}
	}
	return sc.Err()
}

// backoff 对应 _stream_with_retry._delay：Retry-After 优先，否则 1.5*2^attempt 封顶 30s，下限 0.5s。
func (s *OaiSession) backoff(ctx context.Context, resp *http.Response, attempt int) {
	d := 500 * time.Millisecond
	if resp != nil {
		if ra := resp.Header.Get("Retry-After"); ra != "" {
			if secs, err := strconv.ParseFloat(ra, 64); err == nil && secs > 0 {
				d = time.Duration(secs * float64(time.Second))
			}
		}
	}
	if d < 500*time.Millisecond {
		mult := 1.5 * float64(int(1)<<attempt)
		if mult > 30 {
			mult = 30
		}
		d = time.Duration(mult * float64(time.Second))
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
	case <-ctx.Done():
	}
}

// strval / f64val：cfg map 取值（JSON 反序列化后数字为 float64）。
func strval(cfg map[string]any, k string) string {
	if v, ok := cfg[k].(string); ok {
		return v
	}
	return ""
}

func f64val(cfg map[string]any, k string) (float64, bool) {
	if v, ok := cfg[k].(float64); ok {
		return v, true
	}
	return 0, false
}
