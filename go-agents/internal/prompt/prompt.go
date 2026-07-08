// Package prompt 加载 sys_prompt 并注入 Today + 全局记忆。
// 对应 Python agentmain.get_system_prompt()。
package prompt

import (
	"os"
	"path/filepath"
	"strings"
	"time"

	"genericagent/internal/memory"
)

// GetSystemPrompt 拼接 sys_prompt.txt + Today + GetGlobalMemory。
func GetSystemPrompt(root, lang string) string {
	suffix := ""
	if lang == "en" {
		suffix = "_en"
	}

	data, err := os.ReadFile(filepath.Join(root, "assets", "sys_prompt"+suffix+".txt"))
	if err != nil {
		data = []byte{} // 缺失则空（Python 会抛，但 demo 场景容错）
	}

	var b strings.Builder
	b.Write(data)
	b.WriteString("\nToday: " + time.Now().Format("2006-01-02 Mon") + "\n")
	b.WriteString(memory.GetGlobalMemory(root, lang))
	return b.String()
}
