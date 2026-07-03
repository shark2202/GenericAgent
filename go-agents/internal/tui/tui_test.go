package tui

import (
	"strings"
	"testing"

	"github.com/charmbracelet/bubbles/viewport"

	"genericagent/internal/agent"
)

// TestEventFlow 验证 TUI 状态机：submit → chunk×2 → done，busy 翻转 + 输出累积。
// 不启动真终端，只驱动 waitNext Cmd 链 + Update。
func TestEventFlow(t *testing.T) {
	ch := make(chan agent.Event, 4)
	ch <- agent.Chunk{Text: "Hello"}
	ch <- agent.Chunk{Text: " world"}
	ch <- agent.Done{}
	close(ch)

	m := model{curCh: ch, busy: true, verbose: true, vp: newVP()}

	// chunk 1
	msg := waitNext(m.curCh)()
	if cm, ok := msg.(chunkMsg); !ok || string(cm) != "Hello" {
		t.Fatalf("want chunkMsg Hello, got %T %v", msg, msg)
	}
	m2, _ := m.Update(msg)
	m = m2.(model)
	if got := m.cur.String(); got != "Hello" {
		t.Fatalf("cur after chunk1 = %q", got)
	}
	if !m.busy {
		t.Fatal("busy flipped early")
	}

	// chunk 2
	msg = waitNext(m.curCh)()
	m2, _ = m.Update(msg)
	m = m2.(model)
	if got := m.cur.String(); got != "Hello world" {
		t.Fatalf("cur after chunk2 = %q", got)
	}

	// done
	msg = waitNext(m.curCh)()
	if _, ok := msg.(doneMsg); !ok {
		t.Fatalf("want doneMsg, got %T", msg)
	}
	m2, _ = m.Update(msg)
	m = m2.(model)
	if m.busy {
		t.Fatal("busy not cleared on done")
	}
	if m.cur.Len() != 0 {
		t.Fatalf("cur not reset on done: %q", m.cur.String())
	}
	// 末尾应有 separator 行 + 累积的 "Hello world" 行
	joined := strings.Join(m.lines, "\n")
	if !strings.Contains(joined, "Hello world") {
		t.Fatalf("output missing accumulated text: %q", joined)
	}
}

// TestAbortFlow 验证 Ctrl-C 中止：aborted 标记 → chan close → doneMsg 渲染"已中止"。
func TestAbortFlow(t *testing.T) {
	ch := make(chan agent.Event, 1)
	close(ch) // AbortCurrent 取消 ctx → RunLoop 退出 → chan 关闭，无 Done
	m := model{curCh: ch, busy: true, aborted: true, vp: newVP()}

	msg := waitNext(m.curCh)()
	if _, ok := msg.(doneMsg); !ok {
		t.Fatalf("want doneMsg on closed chan, got %T", msg)
	}
	m2, _ := m.Update(msg)
	m = m2.(model)
	if m.busy {
		t.Fatal("busy not cleared after abort")
	}
	if m.aborted {
		t.Fatal("aborted flag not cleared")
	}
	joined := strings.Join(m.lines, "\n")
	if !strings.Contains(joined, "已中止") {
		t.Fatalf("abort marker missing: %q", joined)
	}
}

func newVP() (vp viewport.Model) { return viewport.New(80, 20) }
