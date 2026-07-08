package app

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"genericagent/internal/agent"
	"genericagent/internal/python"
)

// RunCLI 对应 Python CLI 模式：input 循环 + 增量打印。
func RunCLI(a *GenericAgent) {
	go a.Run()
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)
	for {
		fmt.Print("> ")
		if !scanner.Scan() {
			break
		}
		q := strings.TrimSpace(scanner.Text())
		if q == "" {
			continue
		}
		out := a.PutTask(q, "user")
		for ev := range out {
			switch e := ev.(type) {
			case agent.Chunk:
				fmt.Print(e.Text)
			case agent.Done:
				fmt.Println()
			}
		}
	}
}

// RunTask 对应 task 模式：input.txt → output.txt → 等 reply.txt 多轮。
func RunTask(a *GenericAgent, taskName, input string) error {
	go a.Run()
	a.PeerHint = false
	d := filepath.Join(a.Root, "temp", taskName)
	if err := os.MkdirAll(d, 0o755); err != nil {
		return err
	}
	infile := filepath.Join(d, "input.txt")
	var raw string
	if input != "" {
		if err := os.WriteFile(infile, []byte(input), 0o644); err != nil {
			return err
		}
		raw = input
	} else {
		data, err := os.ReadFile(infile)
		if err != nil {
			return err
		}
		raw = string(data)
	}
	nround := 0
	for {
		outfile := filepath.Join(d, "output.txt")
		if nround > 0 {
			outfile = filepath.Join(d, fmt.Sprintf("output%d.txt", nround))
		}
		out := a.PutTask(raw, "task")
		var full string
		for ev := range out {
			if c, ok := ev.(agent.Chunk); ok {
				full += c.Text
				_ = os.WriteFile(outfile, []byte(full), 0o644)
			}
			if dn, ok := ev.(agent.Done); ok {
				full = dn.FullResp
			}
		}
		if err := os.WriteFile(outfile, []byte(full+"\n\n[ROUND END]\n"), 0o644); err != nil {
			return err
		}
		replyPath := filepath.Join(d, "reply.txt")
		raw = waitForFile(replyPath, 10*time.Minute)
		if raw == "" {
			break
		}
		nround++
	}
	return nil
}

// RunFunc 对应 func 模式：读 prompt → 写 .out.txt → 退出。
func RunFunc(a *GenericAgent, promptFile string) error {
	go a.Run()
	a.PeerHint = false
	data, err := os.ReadFile(promptFile)
	if err != nil {
		return err
	}
	raw := string(data)
	outfile := strings.TrimSuffix(promptFile, filepath.Ext(promptFile)) + ".out.txt"
	out := a.PutTask(raw, "func")
	var full string
	for ev := range out {
		if c, ok := ev.(agent.Chunk); ok {
			full += c.Text
			_ = os.WriteFile(outfile, []byte(full), 0o644)
		}
		if dn, ok := ev.(agent.Done); ok {
			full = dn.FullResp
		}
	}
	return os.WriteFile(outfile, []byte(full), 0o644)
}

// RunReflect 对应 reflect 模式：子进程协议轮询 check()，触发任务，on_done 回调。
// PRD §8.4：Go 不动态 import，改子进程。热重载 = 重启子进程。
// ponytail: 子进程调 python3 执行脚本 check()/on_done()；无真脚本时无法验证。
func RunReflect(a *GenericAgent, script string, interval time.Duration, once bool) error {
	go a.Run()
	a.PeerHint = false
	scriptDir := filepath.Dir(script)
	mod := strings.TrimSuffix(filepath.Base(script), filepath.Ext(script))
	logDir := filepath.Join(a.Root, "temp", "reflect_logs")
	_ = os.MkdirAll(logDir, 0o755)
	mt := fileMtime(script)
	fmt.Printf("[Reflect] loaded %s\n", script)
	for {
		if m := fileMtime(script); m != mt {
			mt = m
			fmt.Printf("[Reflect] reloaded %s\n", script)
		}
		task, err := reflectCheck(scriptDir, mod, a.Root)
		if err != nil {
			fmt.Printf("[Reflect] check() error: %v\n", err)
		}
		task = strings.TrimSpace(task)
		if task == "/exit" {
			break
		}
		if task != "" {
			fmt.Printf("[Reflect] triggered: %s\n", truncate(task, 80))
			out := a.PutTask(task, "reflect")
			var result string
			for ev := range out {
				if dn, ok := ev.(agent.Done); ok {
					result = dn.FullResp
				}
			}
			fmt.Println(result)
			logFile := filepath.Join(logDir, fmt.Sprintf("%s_%s.log", mod, time.Now().Format("2006-01-02")))
			_ = os.WriteFile(logFile, []byte(fmt.Sprintf("[%s]\n%s\n\n", time.Now().Format("01-02 15:04"), result)), 0o644)
			if err := reflectOnDone(scriptDir, mod, result, a.Root); err != nil {
				// on_done 可选，忽略缺失
			}
			if once {
				break
			}
		}
		time.Sleep(interval)
	}
	return nil
}

// reflectCheck 子进程调 python3 执行脚本 check()，返回 task 字符串。
func reflectCheck(scriptDir, mod, root string) (string, error) {
	py := fmt.Sprintf(`import sys; sys.path.insert(0,%q); import %s as m
r = m.check() if hasattr(m,'check') else None
print(r if r else "")`, scriptDir, mod)
	out, err := exec.Command(python.Resolve(root), "-c", py).Output()
	if err != nil {
		return "", err
	}
	return string(out), nil
}

// reflectOnDone 子进程调 python3 执行 on_done(result)，result 经临时文件传。
func reflectOnDone(scriptDir, mod, result, root string) error {
	tmp, err := os.CreateTemp("", "reflect-*.txt")
	if err != nil {
		return err
	}
	tmp.WriteString(result)
	tmp.Close()
	defer os.Remove(tmp.Name())
	py := fmt.Sprintf(`import sys; sys.path.insert(0,%q); import %s as m
if hasattr(m,'on_done'): m.on_done(open(sys.argv[1]).read())`, scriptDir, mod)
	return exec.Command(python.Resolve(root), "-c", py, tmp.Name()).Run()
}

func waitForFile(path string, timeout time.Duration) string {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if data, err := os.ReadFile(path); err == nil && len(data) > 0 {
			_ = os.Remove(path)
			return string(data)
		}
		time.Sleep(2 * time.Second)
	}
	return ""
}

func fileMtime(p string) time.Time {
	fi, err := os.Stat(p)
	if err != nil {
		return time.Time{}
	}
	return fi.ModTime()
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
