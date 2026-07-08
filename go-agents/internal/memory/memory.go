// Package memory 实现 L0-L4 分层记忆的读写（Phase 0）。
// 记忆文件格式与 Python 版完全一致，双向兼容。
package memory

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// InitMemory 确保 memory 目录与 L1/L2 文件存在；不存在则从 template 初始化。
// 对应 Python agentmain.py 启动时的 memory 初始化逻辑。
func InitMemory(root, lang string) error {
	memDir := filepath.Join(root, "memory")
	if err := os.MkdirAll(memDir, 0o755); err != nil {
		return err
	}

	// L2: global_mem.txt
	l2 := filepath.Join(memDir, "global_mem.txt")
	if _, err := os.Stat(l2); os.IsNotExist(err) {
		if err := os.WriteFile(l2, []byte("# [Global Memory - L2]\n"), 0o644); err != nil {
			return err
		}
	}

	// L1: global_mem_insight.txt ← template
	l1 := filepath.Join(memDir, "global_mem_insight.txt")
	if _, err := os.Stat(l1); os.IsNotExist(err) {
		suffix := ""
		if lang == "en" {
			suffix = "_en"
		}
		tmpl := filepath.Join(root, "assets", "global_mem_insight_template"+suffix+".txt")
		data, err := os.ReadFile(tmpl)
		if err != nil {
			data = []byte{} // template 缺失 → 空（与 Python 行为一致）
		}
		if err := os.WriteFile(l1, data, 0o644); err != nil {
			return err
		}
	}

	// temp 目录（cwd 约定）
	if err := os.MkdirAll(filepath.Join(root, "temp"), 0o755); err != nil {
		return err
	}
	return nil
}

// LogMemoryAccess 更新 memory/file_access_stats.json（count + last date）。
// 仅当 path 含 "memory" 时记录。对应 Python ga.log_memory_access。
func LogMemoryAccess(path, root string) {
	if !strings.Contains(path, "memory") {
		return
	}
	statsFile := filepath.Join(root, "memory", "file_access_stats.json")
	stats := map[string]map[string]any{}
	if data, err := os.ReadFile(statsFile); err == nil {
		_ = json.Unmarshal(data, &stats)
	}
	fname := filepath.Base(path)
	entry := stats[fname]
	if entry == nil {
		entry = map[string]any{}
	}
	cnt, _ := entry["count"].(float64)
	entry["count"] = int(cnt) + 1
	entry["last"] = time.Now().Format("2006-01-02")
	stats[fname] = entry
	b, _ := json.MarshalIndent(stats, "", "  ")
	_ = os.WriteFile(statsFile, b, 0o644)
}

// GetGlobalMemory 拼接 L1 索引 + 固定结构，注入 system prompt。
// 对应 Python ga.get_global_memory()，逐行等价。
func GetGlobalMemory(root, lang string) string {
	suffix := ""
	if lang == "en" {
		suffix = "_en"
	}

	insightPath := filepath.Join(root, "memory", "global_mem_insight.txt")
	insight, err := os.ReadFile(insightPath)
	if err != nil {
		return "\n" // FileNotFoundError → pass → 返回 "\n"
	}
	LogMemoryAccess(insightPath, root)
	structure, err := os.ReadFile(filepath.Join(root, "assets", "insight_fixed_structure"+suffix+".txt"))
	if err != nil {
		return "\n"
	}

	var b strings.Builder
	b.WriteString("\n")
	b.WriteString("cwd = " + filepath.Join(root, "temp") + " (./)\n")
	b.WriteString("\n[Memory] (../memory)\n")
	b.WriteString(string(structure) + "\n../memory/global_mem_insight.txt:\n")
	b.WriteString(string(insight) + "\n")
	return b.String()
}
