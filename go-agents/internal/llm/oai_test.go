package llm

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAutoMakeURL(t *testing.T) {
	cases := map[string]string{
		"http://host:2001":                     "http://host:2001/v1/chat/completions",
		"http://host:2001/v1":                  "http://host:2001/v1/chat/completions",
		"http://host:2001/v1/chat/completions": "http://host:2001/v1/chat/completions",
		"https://api.openai.com":               "https://api.openai.com/v1/chat/completions",
		"http://host$":                         "http://host",
	}
	for base, want := range cases {
		if got := autoMakeURL(base, "chat/completions"); got != want {
			t.Errorf("autoMakeURL(%q)=%q want %q", base, got, want)
		}
	}
}

func TestOaiSessionStream(t *testing.T) {
	body := strings.Join([]string{
		`data: {"choices":[{"delta":{"content":"Hello"}}]}`,
		``,
		`data: {"choices":[{"delta":{"content":" world"}}]}`,
		``,
		`data: {"choices":[{"delta":{},"finish_reason":"stop"}]}`,
		``,
		`data: [DONE]`,
		``,
	}, "\n")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer sk-test" {
			t.Errorf("missing bearer: %q", r.Header.Get("Authorization"))
		}
		if r.Header.Get("Accept") != "text/event-stream" {
			t.Errorf("missing accept")
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(200)
		fmt.Fprint(w, body)
	}))
	defer srv.Close()
	s := &OaiSession{apiKey: "sk-test", apiBase: srv.URL, model: "gpt-test", temperature: 1.0, maxRetries: 0, client: srv.Client()}
	var got strings.Builder
	for chunk := range s.Ask(context.Background(), "ping") {
		got.WriteString(chunk)
	}
	if got.String() != "Hello world" {
		t.Fatalf("got %q want %q", got.String(), "Hello world")
	}
	if len(s.history) != 1 || s.history[0].Content != "Hello world" {
		t.Errorf("history not appended: %v", s.history)
	}
}

func TestOaiSessionStreamWithToolUse(t *testing.T) {
	// 模型在 content 文本里输出 <tool_use> 块（文本协议），OAI 只管吐文本，解析归 ToolClient。
	tu := `<tool_use>{"name":"code_run","arguments":{"type":"python","code":"print(1)"}}</tool_use>`
	body := strings.Join([]string{
		`data: {"choices":[{"delta":{"content":"<summary>x</summary>\n"}}]}`,
		``,
		fmt.Sprintf(`data: {"choices":[{"delta":{"content":%s}}]}`, mustJSONString(tu)),
		``,
		`data: [DONE]`,
		``,
	}, "\n")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		fmt.Fprint(w, body)
	}))
	defer srv.Close()
	s := &OaiSession{apiKey: "sk-test", apiBase: srv.URL, model: "gpt-test", temperature: 1.0, maxRetries: 0, client: srv.Client()}
	var got strings.Builder
	for chunk := range s.Ask(context.Background(), "ping") {
		got.WriteString(chunk)
	}
	resp := ParseMixedResponse(got.String())
	if len(resp.ToolCalls) != 1 || resp.ToolCalls[0].Name != "code_run" {
		t.Fatalf("toolcalls parse failed: %+v (raw %q)", resp.ToolCalls, got.String())
	}
}

func TestOaiSessionHTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(429)
		fmt.Fprint(w, `{"error":"rate limited"}`)
	}))
	defer srv.Close()
	s := &OaiSession{apiKey: "sk-test", apiBase: srv.URL, model: "gpt-test", temperature: 1.0, maxRetries: 0, client: srv.Client()}
	var got strings.Builder
	for chunk := range s.Ask(context.Background(), "ping") {
		got.WriteString(chunk)
	}
	if !strings.Contains(got.String(), "!!!Error: HTTP 429") {
		t.Fatalf("got %q want 429 error", got.String())
	}
}

func TestNewOaiSessionFromCfg(t *testing.T) {
	cfg := map[string]any{
		"apikey": "sk-x", "apibase": "https://api.openai.com/v1", "model": "gpt-5",
		"temperature": 0.5, "max_tokens": float64(8192), "reasoning_effort": "high", "max_retries": float64(3),
		"name": "gpt-test",
	}
	s, err := NewOaiSessionFromCfg(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if s.apiKey != "sk-x" || s.model != "gpt-5" || s.name != "gpt-test" {
		t.Errorf("fields wrong: %+v", s)
	}
	if s.temperature != 0.5 {
		t.Errorf("temp %v want 0.5", s.temperature)
	}
	if s.maxTokens == nil || *s.maxTokens != 8192 {
		t.Errorf("maxTokens %v want 8192", s.maxTokens)
	}
	if s.maxRetries != 3 {
		t.Errorf("retries %v want 3", s.maxRetries)
	}
	// gpt-5 → max_completion_tokens
	payload, _ := s.buildPayload("x")
	if v, ok := payload["max_completion_tokens"]; !ok || v.(int) != 8192 {
		t.Errorf("gpt-5 should use max_completion_tokens, got %v", payload)
	}
	if v, ok := payload["temperature"]; !ok || v.(float64) != 0.5 {
		t.Errorf("temperature not in payload: %v", payload)
	}
}

func TestLoadMykeys(t *testing.T) {
	dir := t.TempDir()
	jsonc := `{
  // OpenAI 兼容（chat_completions 流式）
  "native_oai_config": {
    "name": "gpt-dummy",
    "apikey": "sk-dummy",
    "apibase": "https://api.openai.com/v1",  // 注释里的 // 和 URL 的 https:// 都安全
    "model": "gpt-test",
    "temperature": 0.7,
    "max_tokens": 4096,
    "max_retries": 2,
    "reasoning_effort": "medium"
  },
  /* 块注释：渠道组（自动故障转移）
     多行说明 */
  "mixin_config": {
    "llm_nos": ["gpt-dummy"],
    "max_retries": 10,
    "base_delay": 0.5
  }
}`
	if err := os.WriteFile(filepath.Join(dir, "mykey.jsonc"), []byte(jsonc), 0644); err != nil {
		t.Fatal(err)
	}
	cfgs, err := LoadMykeys(dir)
	if err != nil {
		t.Fatal(err)
	}
	cfg, ok := cfgs["native_oai_config"]
	if !ok {
		t.Fatalf("native_oai_config missing: %v", cfgs)
	}
	if cfg["apikey"] != "sk-dummy" {
		t.Errorf("apikey %v", cfg["apikey"])
	}
	// URL 必须未被注释剥离损坏
	if cfg["apibase"] != "https://api.openai.com/v1" {
		t.Errorf("apibase corrupted by comment strip: %v", cfg["apibase"])
	}
	if mx, ok := cfgs["mixin_config"]; !ok || mx["base_delay"] != 0.5 {
		t.Errorf("mixin missing/wrong: %v", cfgs["mixin_config"])
	}
	s, err := NewOaiSessionFromCfg(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if s.model != "gpt-test" || s.apiKey != "sk-dummy" {
		t.Errorf("session wrong: %+v", s)
	}
}

// TestStripJSONC 验证注释剥离不破坏字符串内的 // （apibase 的 https:// ）。
func TestStripJSONC(t *testing.T) {
	in := `{"url": "https://api.openai.com/v1", // 行注释
"a": 1 /* 块注释 */}`
	out := string(stripJSONC([]byte(in)))
	if !strings.Contains(out, "https://api.openai.com/v1") {
		t.Errorf("URL 被 // 剥离吃掉: %q", out)
	}
	if strings.Contains(out, "行注释") || strings.Contains(out, "块注释") {
		t.Errorf("注释未去除: %q", out)
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		t.Errorf("结果非合法 JSON: %v (%q)", err, out)
	}
	if m["url"] != "https://api.openai.com/v1" {
		t.Errorf("url 值错: %v", m["url"])
	}
}

// mustJSONString 把字符串编码为 JSON 字符串字面量（含引号），用于 SSE body 拼接。
func mustJSONString(s string) string {
	b, err := json.Marshal(s)
	if err != nil {
		panic(err)
	}
	return string(b)
}
