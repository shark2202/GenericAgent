package tools

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"

	"genericagent/internal/agent"
	"genericagent/internal/loop"
	"genericagent/internal/python"
)

// Bridge 管理 tmwebdriver_bridge.py 持久子进程，经 stdin/stdout NDJSON 转发命令。
// 对应 PRD §8.3 A 层（Go 静态骨架 + Python 动态主体）。
type Bridge struct {
	ScriptPath string
	Root       string

	mu      sync.Mutex
	cmd     *exec.Cmd
	stdin   *os.File
	stdout  *bufio.Reader
	started bool
}

// NewBridge 默认脚本 root/assets/tmwebdriver_bridge.py。
func NewBridge(root string) *Bridge {
	return &Bridge{Root: root, ScriptPath: filepath.Join(root, "assets", "tmwebdriver_bridge.py")}
}

func (b *Bridge) start() error {
	cmd := exec.Command(python.Resolve(b.Root), "-X", "utf8", "-u", b.ScriptPath, b.Root)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("bridge start: %w", err)
	}
	b.cmd = cmd
	b.stdin = stdin.(*os.File)
	b.stdout = bufio.NewReaderSize(stdout, 1<<20)
	// 健康检查：ping 不触发 driver 初始化。
	if _, err := b.callLocked(map[string]any{"cmd": "ping"}); err != nil {
		b.kill()
		return fmt.Errorf("bridge ping: %w", err)
	}
	return nil
}

func (b *Bridge) kill() {
	if b.cmd != nil && b.cmd.Process != nil {
		_ = b.cmd.Process.Kill()
	}
}

// Call 发一条命令，收一条响应。线程安全（串行化）。
func (b *Bridge) Call(req map[string]any) (map[string]any, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if !b.started {
		if err := b.start(); err != nil {
			return nil, err
		}
		b.started = true
	}
	return b.callLocked(req)
}

func (b *Bridge) callLocked(req map[string]any) (map[string]any, error) {
	line, _ := json.Marshal(req)
	if _, err := b.stdin.Write(append(line, '\n')); err != nil {
		return nil, err
	}
	resp, err := b.stdout.ReadString('\n')
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(strings.TrimRight(resp, "\n")), &out); err != nil {
		return nil, err
	}
	return out, nil
}

// Close 关闭子进程（关闭 stdin → bridge 的 for 循环退出 → 进程退出）。
func (b *Bridge) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.stdin != nil {
		_ = b.stdin.Close()
	}
	b.kill()
}

// ---- WebScan：对应 ga.do_web_scan ----

type WebScan struct{ B *Bridge }

func (WebScan) Name() string { return "web_scan" }
func (w WebScan) Dispatch(ctx context.Context, args map[string]any, resp agent.Response) agent.Stream[agent.ToolEvent] {
	ch := make(chan agent.ToolEvent, 4)
	go func() {
		defer close(ch)
		toolNum := toInt(args["_tool_num"], 1)
		if toolNum < 1 {
			toolNum = 1
		}
		maxlen := 35000 / toolNum
		if maxlen < 1000 {
			maxlen = 1000
		}
		req := map[string]any{
			"cmd": "scan", "tabs_only": args["tabs_only"], "switch_tab_id": args["switch_tab_id"],
			"text_only": args["text_only"], "maxlen": maxlen,
		}
		result, err := w.B.Call(req)
		if err != nil {
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": err.Error()}, NextPrompt: "\n"}}
			return
		}
		content, _ := result["content"].(string)
		delete(result, "content")
		ch <- agent.ToolEvent{Log: "[Info] " + jsonMarshalNoEscape(result) + "\n"}
		var data string
		if content != "" {
			data = jsonMarshalNoEscape(result) + "\n```html\n" + content + "\n```"
		} else {
			data = jsonMarshalNoEscape(result)
		}
		ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: data, NextPrompt: "\n"}}
	}()
	return ch
}

// ---- WebExecuteJS：对应 ga.do_web_execute_js ----

type WebExecuteJS struct{ B *Bridge }

func (WebExecuteJS) Name() string { return "web_execute_js" }
func (w WebExecuteJS) Dispatch(ctx context.Context, args map[string]any, resp agent.Response) agent.Stream[agent.ToolEvent] {
	ch := make(chan agent.ToolEvent, 4)
	go func() {
		defer close(ch)
		hh, _ := args["_handler"].(*loop.Handler)
		cwd, _ := args["_cwd"].(string)
		script, _ := args["script"].(string)
		if script == "" {
			script = extractCodeBlock(resp.Content, "javascript")
		}
		if script == "" {
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: "[Error] Script missing. Use ```javascript block or 'script' arg.", NextPrompt: "\n"}}
			return
		}
		if absPath := joinPath(cwd, strings.TrimSpace(script)); isFile(absPath) {
			if data, err := os.ReadFile(absPath); err == nil {
				script = string(data)
			}
		}
		switchTab, _ := args["switch_tab_id"].(string)
		if switchTab == "" {
			switchTab, _ = args["tab_id"].(string)
		}
		req := map[string]any{
			"cmd": "execute_js", "script": script, "switch_tab_id": switchTab,
			"no_monitor": args["no_monitor"],
		}
		result, err := w.B.Call(req)
		if err != nil {
			ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: map[string]any{"error": err.Error()}, NextPrompt: "\n"}}
			return
		}
		if saveTo, _ := args["save_to_file"].(string); saveTo != "" {
			if jsRet, ok := result["js_return"]; ok {
				content := fmt.Sprint(jsRet)
				saveAbs := joinPath(cwd, saveTo)
				result["js_return"] = smartFormat(content, 170)
				if err := os.WriteFile(saveAbs, []byte(content), 0o644); err == nil {
					result["js_return"] = result["js_return"].(string) + "\n\n[已保存完整内容到 " + saveAbs + "]"
				} else {
					result["js_return"] = result["js_return"].(string) + "\n\n[保存失败，无法写入文件 " + saveAbs + "]"
				}
			}
		}
		indented := jsonMarshalIndentNoEscape(result)
		show := smartFormat(indented, 300)
		ch <- agent.ToolEvent{Log: "JS 执行结果:\n" + show + "\n"}
		toolNum := toInt(args["_tool_num"], 1)
		if toolNum < 1 {
			toolNum = 1
		}
		maxlen := 8000 / toolNum
		skip := toInt(args["_index"], 0) > 0
		nextPrompt := ""
		if hh != nil {
			nextPrompt = hh.GetAnchorPrompt(skip)
		}
		ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: smartFormat(jsonMarshalNoEscape(result), maxlen), NextPrompt: nextPrompt}}
	}()
	return ch
}

// ---- helpers ----

func smartFormat(data string, maxStrLen int) string {
	omit := " ... "
	runes := []rune(data)
	if len(runes) < maxStrLen+len(omit)*2 {
		return data
	}
	half := maxStrLen / 2
	return string(runes[:half]) + omit + string(runes[len(runes)-half:])
}

func extractCodeBlock(content, codeType string) string {
	pat := regexp.QuoteMeta(codeType)
	switch codeType {
	case "python":
		pat = "python|py"
	case "powershell":
		pat = "powershell|ps1|pwsh"
	case "bash":
		pat = "bash|sh|shell"
	}
	re := regexp.MustCompile("(?s)```(?:" + pat + ")\n(.*?)\n```")
	ms := re.FindAllStringSubmatch(content, -1)
	if len(ms) == 0 {
		return ""
	}
	return strings.TrimSpace(ms[len(ms)-1][1])
}

func jsonMarshalNoEscape(v any) string {
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return strings.TrimRight(buf.String(), "\n")
}

func jsonMarshalIndentNoEscape(v any) string {
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
	return strings.TrimRight(buf.String(), "\n")
}

func isFile(p string) bool {
	info, err := os.Stat(p)
	return err == nil && !info.IsDir()
}
