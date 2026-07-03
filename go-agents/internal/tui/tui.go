// Package tui 提供 bubbletea 交互前端（PRD §3.4.4 A 阶段）。
// 消费 GenericAgent.PutTask 返回的 Event chan，流式渲染 + 工具日志 + 输入行。
// 已知限制：ask_user 工具直读 os.Stdin，TUI 下会挂（用 --plain 跑需要 ask_user 的任务）。
package tui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"genericagent/internal/agent"
	"genericagent/internal/app"
)

// ── styles ──────────────────────────────────────────────
var (
	user   = lipgloss.NewStyle().Foreground(lipgloss.Color("39")).Bold(true)
	ai     = lipgloss.NewStyle().Foreground(lipgloss.Color("252"))
	logs   = lipgloss.NewStyle().Foreground(lipgloss.Color("241"))
	sep    = lipgloss.NewStyle().Foreground(lipgloss.Color("238"))
	status = lipgloss.NewStyle().Foreground(lipgloss.Color("245"))
	errS   = lipgloss.NewStyle().Foreground(lipgloss.Color("203")).Bold(true)
)

// ── model ───────────────────────────────────────────────
type model struct {
	a       *app.GenericAgent
	input   textinput.Model
	vp      viewport.Model
	lines   []string
	cur     strings.Builder
	turn    int
	busy    bool
	aborted bool
	verbose bool
	curCh   chan agent.Event
}

// ── messages ────────────────────────────────────────────
type chunkMsg string
type logMsg string
type turnMsg int
type doneMsg struct{ err error }

// waitNext 从 event chan 读一条，转成 tea.Msg。chan 关闭时返回 doneMsg{}。
func waitNext(ch chan agent.Event) tea.Cmd {
	return func() tea.Msg {
		ev, ok := <-ch
		if !ok {
			return doneMsg{}
		}
		switch e := ev.(type) {
		case agent.Chunk:
			return chunkMsg(e.Text)
		case agent.ToolLog:
			return logMsg(e.Text)
		case agent.TurnStart:
			return turnMsg(e.Turn)
		case agent.Done:
			return doneMsg{err: e.Err}
		}
		return nil
	}
}

// Init 满足 tea.Model。
func (m model) Init() tea.Cmd {
	return textinput.Blink
}

// Update 满足 tea.Model。
func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.vp.Width = max(msg.Width-2, 40)
		m.vp.Height = max(msg.Height-3, 3)
		m.input.Width = max(msg.Width-3, 20)
		return m, nil

	case chunkMsg:
		m.cur.WriteString(string(msg))
		m.render()
		return m, waitNext(m.curCh)

	case logMsg:
		if m.verbose {
			m.lines = append(m.lines, logs.Render("▌ "+string(msg)))
			m.render()
		}
		return m, waitNext(m.curCh)

	case turnMsg:
		m.turn = int(msg)
		return m, waitNext(m.curCh)

	case doneMsg:
		m.busy = false
		if m.aborted {
			m.lines = append(m.lines, errS.Render("✗ 已中止"))
			m.aborted = false
		} else {
			if m.cur.Len() > 0 {
				m.lines = append(m.lines, ai.Render(m.cur.String()))
			}
			if msg.err != nil {
				m.lines = append(m.lines, errS.Render("✗ "+msg.err.Error()))
			}
		}
		m.cur.Reset()
		m.lines = append(m.lines, sep.Render(strings.Repeat("─", 8)))
		m.render()
		return m, nil

	case tea.KeyMsg:
		switch msg.String() {
		case "ctrl+c":
			if m.busy {
				m.a.AbortCurrent()
				m.aborted = true
				return m, nil // 等 chan close → doneMsg
			}
			return m, tea.Quit
		case "esc":
			if !m.busy {
				return m, tea.Quit
			}
		case "pgup", "pgdown":
			var cmd tea.Cmd
			m.vp, cmd = m.vp.Update(msg)
			return m, cmd
		case "enter":
			if m.busy {
				return m, nil
			}
			q := strings.TrimSpace(m.input.Value())
			if q == "" {
				return m, nil
			}
			m.input.SetValue("")
			m.busy = true
			m.lines = append(m.lines, user.Render("> "+q))
			m.cur.Reset()
			m.render()
			m.curCh = m.a.PutTask(q, "user")
			return m, waitNext(m.curCh)
		}
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		return m, cmd
	}
	return m, nil
}

// View 满足 tea.Model。
func (m model) View() string {
	st := fmt.Sprintf("○ idle · turn %d", m.turn)
	if m.busy {
		st = fmt.Sprintf("● busy · turn %d", m.turn)
	}
	return strings.Join([]string{
		m.vp.View(),
		status.Render(st),
		"> " + m.input.View(),
	}, "\n")
}

// render 将 lines + cur 写入 viewport，自动滚到底。
func (m *model) render() {
	c := strings.Join(m.lines, "\n")
	if m.cur.Len() > 0 {
		if c != "" {
			c += "\n"
		}
		c += ai.Render(m.cur.String())
	}
	m.vp.SetContent(c)
	m.vp.GotoBottom()
}

// Run 启动 bubbletea TUI（alt screen）。agent.Run() 在此 goroutine 中启动。
func Run(a *app.GenericAgent, verbose bool) error {
	ti := textinput.New()
	ti.Placeholder = "输入任务，Enter 发送 · Ctrl-C 中止当前 · Esc 退出"
	ti.Focus()
	ti.CharLimit = 0

	vp := viewport.New(80, 20)
	m := model{a: a, input: ti, vp: vp, verbose: verbose}

	go a.Run()

	p := tea.NewProgram(m, tea.WithAltScreen())
	_, err := p.Run()
	return err
}
