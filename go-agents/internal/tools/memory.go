package tools

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"genericagent/internal/agent"
	"genericagent/internal/loop"
	"genericagent/internal/memory"
)

// UpdateWorkingCheckpoint 对应 do_update_working_checkpoint：写 working memory + 返回 anchor prompt。
type UpdateWorkingCheckpoint struct{}

func (UpdateWorkingCheckpoint) Name() string { return "update_working_checkpoint" }
func (UpdateWorkingCheckpoint) Dispatch(ctx context.Context, args map[string]any, resp agent.Response) agent.Stream[agent.ToolEvent] {
	ch := make(chan agent.ToolEvent, 4)
	hh, _ := args["_handler"].(*loop.Handler)
	go func() {
		defer close(ch)
		if ki, ok := args["key_info"]; ok {
			hh.SetKeyInfo(fmt.Sprint(ki))
		}
		if rs, ok := args["related_sop"]; ok {
			hh.SetRelatedSop(fmt.Sprint(rs))
		}
		hh.Working["passed_sessions"] = "0"
		ch <- agent.ToolEvent{Log: "[Info] Updated key_info and related_sop.\n"}
		skip := toInt(args["_index"], 0) > 0
		ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{
			Data:       map[string]any{"result": "working key_info updated"},
			NextPrompt: hh.GetAnchorPrompt(skip),
		}}
	}()
	return ch
}

// StartLongTermUpdate 对应 do_start_long_term_update：读 L0 SOP + 蒸馏指令。
type StartLongTermUpdate struct{ Root, Lang string }

const longTermPrompt = `### [总结提炼经验] 既然你觉得当前任务有重要信息需要记忆，请提取最近一次任务中【事实验证成功且长期有效】的环境事实、用户偏好、重要步骤，更新记忆。
本工具是标记开启结算过程，若已在更新记忆过程或没有值得记忆的点，忽略本次调用。
**如果没有经验证的，未来能用上的信息，忽略本次调用！**
**只能提取行动验证成功的信息**：
- **环境事实**（路径/凭证/配置）→ ` + "`file_patch`" + ` 更新 L2，同步 L1
- **复杂任务经验**（关键坑点/前置条件/重要步骤）→ L3 精简 SOP（只记你被坑得多次重试的核心要点）
**禁止**：临时变量、具体推理过程、未验证信息、通用常识、你可以轻松复现的细节、只是做了但没有验证的信息
**操作**：严格遵循提供的L0的记忆更新SOP。先 ` + "`file_read`" + ` 看现有 → 判断类型 → 最小化更新 → 无新内容跳过，保证对记忆库最小局部修改。
`

func (StartLongTermUpdate) Name() string { return "start_long_term_update" }
func (s StartLongTermUpdate) Dispatch(ctx context.Context, args map[string]any, resp agent.Response) agent.Stream[agent.ToolEvent] {
	ch := make(chan agent.ToolEvent, 4)
	go func() {
		defer close(ch)
		ch <- agent.ToolEvent{Log: "[Info] Start distilling good memory for long-term storage.\n"}
		path := filepath.Join(s.Root, "memory", "memory_management_sop.md")
		var result string
		if data, err := os.ReadFile(path); err == nil {
			memory.LogMemoryAccess(path, s.Root)
			result = "This is L0:\n" + string(data)
		} else {
			result = "Memory Management SOP not found. Do not update memory."
		}
		prompt := longTermPrompt + "\n" + memory.GetGlobalMemory(s.Root, s.Lang)
		ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: result, NextPrompt: prompt}}
	}()
	return ch
}

// AskUser 对应 do_ask_user：读 stdin，should_exit（等用户重新发起任务）。
// Phase 2：自由文本输入；candidates 候选选择 Phase 3 再加。
type AskUser struct{}

func (AskUser) Name() string { return "ask_user" }
func (AskUser) Dispatch(ctx context.Context, args map[string]any, resp agent.Response) agent.Stream[agent.ToolEvent] {
	ch := make(chan agent.ToolEvent, 2)
	go func() {
		defer close(ch)
		question, _ := args["question"].(string)
		if strings.TrimSpace(question) == "" {
			question = "请提供输入："
		}
		ch <- agent.ToolEvent{Log: "Waiting for your answer ...\n"}
		fmt.Fprint(os.Stdout, "[ask_user] "+question+"\n> ")
		reader := bufio.NewReader(os.Stdin)
		line, _ := reader.ReadString('\n')
		answer := strings.TrimRight(line, "\n")
		ch <- agent.ToolEvent{Outcome: &agent.StepOutcome{Data: answer, NextPrompt: "", ShouldExit: true}}
	}()
	return ch
}
