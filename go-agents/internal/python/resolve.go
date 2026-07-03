// Package python 解析 Python 解释器路径（bundle 优先，系统兜底）。
// 对应 PRD §8.5：Go 二进制自包含，子进程调用点共用此解析器。
package python

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

// Resolve 返回 Python 解释器路径。优先级：
//  1. GA_PYTHON 环境变量（显式覆盖）
//  2. <exe_dir>/python/... （分发时随二进制）
//  3. <root>/python-bundle/python/... （bundle 放 GA root 时）
//  4. <cwd>/python-bundle/python/... （dev：go run 从 go-agents/ 启动，bundle 在此）
//  5. 系统 python3 / python（dev fallback）
func Resolve(root string) string {
	if p := os.Getenv("GA_PYTHON"); p != "" {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	if exe, err := os.Executable(); err == nil {
		p := bundleBin(filepath.Join(filepath.Dir(exe), "python"))
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	if root != "" {
		p := bundleBin(filepath.Join(root, "python-bundle", "python"))
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	if cwd, err := os.Getwd(); err == nil {
		p := bundleBin(filepath.Join(cwd, "python-bundle", "python"))
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	if p, err := exec.LookPath("python3"); err == nil {
		return p
	}
	if p, err := exec.LookPath("python"); err == nil {
		return p
	}
	return "python3"
}

func bundleBin(bundleDir string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(bundleDir, "python.exe")
	}
	return filepath.Join(bundleDir, "bin", "python3")
}
