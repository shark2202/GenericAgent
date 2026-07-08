// cmd/ga 是 GenericAgent-Go 入口。4 模式：CLI(默认)/task/func/reflect。
// Phase 3：Mock LLM 驱动（真 LLM 待 mykey 配置）。
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"genericagent/internal/agent"
	"genericagent/internal/app"
	"genericagent/internal/llm"
	"genericagent/internal/memory"
	"genericagent/internal/tools"
	"genericagent/internal/tui"
)

func main() {
	var taskDir, funcFile, reflectScript, input, llmCfg string
	var verbose, plain bool
	flag.StringVar(&taskDir, "task", "", "task mode: IO dir name under temp/")
	flag.StringVar(&funcFile, "func", "", "func mode: prompt file")
	flag.StringVar(&reflectScript, "reflect", "", "reflect mode: monitor script")
	flag.StringVar(&input, "input", "", "prompt (task mode)")
	flag.StringVar(&llmCfg, "llm", "", "LLM cfg name in mykey (e.g. native_oai_config); empty=Mock")
	flag.BoolVar(&verbose, "verbose", false, "verbose")
	flag.BoolVar(&plain, "plain", false, "plain line REPL (skip TUI)")
	flag.Parse()

	root := os.Getenv("GA_ROOT")
	if root == "" {
		root = ".."
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	lang := os.Getenv("GA_LANG")
	if err := memory.InitMemory(abs, lang); err != nil {
		fmt.Fprintf(os.Stderr, "InitMemory: %v\n", err)
		os.Exit(1)
	}

	// LLM 后端：--llm <cfg_name> 走 mykey + 真后端；空 = Mock（self-evolving 闭环 demo）
	backend, err := resolveBackend(llmCfg, abs)
	if err != nil {
		fmt.Fprintf(os.Stderr, "llm: %v\n", err)
		os.Exit(1)
	}
	client := llm.NewToolClient(backend, lang)

	// Phase 4: web 工具经 TMWebDriver 子进程桥接（PRD §8.3 A 层）
	bridge := tools.NewBridge(abs)
	defer bridge.Close()

	toolsList := []agent.Tool{
		tools.NoTool{}, tools.FileWrite{}, tools.FileRead{}, tools.FilePatch{}, tools.CodeRun{},
		tools.UpdateWorkingCheckpoint{}, tools.StartLongTermUpdate{Root: abs, Lang: lang}, tools.AskUser{},
		tools.WebScan{B: bridge}, tools.WebExecuteJS{B: bridge},
	}
	a, err := app.NewGenericAgent(abs, lang, client, toolsList)
	if err != nil {
		fmt.Fprintf(os.Stderr, "init: %v\n", err)
		os.Exit(1)
	}
	a.Verbose = verbose

	switch {
	case taskDir != "":
		if err := app.RunTask(a, taskDir, input); err != nil {
			fmt.Fprintf(os.Stderr, "task: %v\n", err)
			os.Exit(1)
		}
	case funcFile != "":
		if err := app.RunFunc(a, funcFile); err != nil {
			fmt.Fprintf(os.Stderr, "func: %v\n", err)
			os.Exit(1)
		}
	case reflectScript != "":
		if err := app.RunReflect(a, reflectScript, 5*time.Second, false); err != nil {
			fmt.Fprintf(os.Stderr, "reflect: %v\n", err)
			os.Exit(1)
		}
	default:
		if !plain && isTTY(os.Stdin) && isTTY(os.Stdout) {
			if err := tui.Run(a, verbose); err != nil {
				fmt.Fprintf(os.Stderr, "tui: %v\n", err)
				os.Exit(1)
			}
		} else {
			app.RunCLI(a)
		}
	}
}

// isTTY 报告 f 是否为终端（stdlib，无 x/term 依赖）。
func isTTY(f *os.File) bool {
	fi, err := f.Stat()
	if err != nil {
		return false
	}
	return (fi.Mode() & os.ModeCharDevice) != 0
}

// resolveBackend：--llm 为空返回 Mock；否则从 mykey 加载 cfg，按名字分发到对应 Session。
// 目前仅 native_oai（含 "oai"）实现；claude/mixin 待 key（PRD §3.5）。
func resolveBackend(llmCfg, root string) (agent.Session, error) {
	if llmCfg == "" {
		return llm.NewMockSession(defaultMockResponses()), nil
	}
	cfg, err := llm.ResolveCfg(root, llmCfg)
	if err != nil {
		return nil, err
	}
	name := strings.ToLower(llmCfg)
	switch {
	case strings.Contains(name, "oai"):
		return llm.NewOaiSessionFromCfg(cfg)
	default:
		return nil, fmt.Errorf("cfg %q: 仅支持 oai session（claude/mixin 待 key）", llmCfg)
	}
}

// defaultMockResponses：self-evolving 闭环 demo（code_run 验证 → file_write L3 → start_long_term_update 蒸馏 → 结束）。
func defaultMockResponses() []string {
	return []string{
		"<summary>验证事实</summary>\n<tool_use>{\"name\":\"code_run\",\"arguments\":{\"type\":\"python\",\"code\":\"print(2**10)\"}}</tool_use>",
		"<summary>写L3技能</summary>\n<tool_use>{\"name\":\"file_write\",\"arguments\":{\"path\":\"../memory/new_skill.md\",\"content\":\"# New Skill\\n验证: 2^10=1024\"}}</tool_use>",
		"<summary>蒸馏记忆</summary>\n<tool_use>{\"name\":\"start_long_term_update\",\"arguments\":{}}</tool_use>",
		"任务完成，已沉淀技能。",
	}
}
