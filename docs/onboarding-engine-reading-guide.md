# 核心引擎阅读指南（接手 onboarding）

> 生成于 2026-07-19 新所有者接手 onboarding。配套验证产物：`temp/smoke_prompt.out.txt`（`--func` 端到端冒烟）。
> 目的：把已验证的端到端链路（`--func` → `SMOKE_OK`）上每个环节钉到行号，按依赖顺序读。

---

## 已验证链路（行号对照）

```
mykey.jsonc → llmcore._load_mykeys(:54)
           → reload_mykeys(:92) → resolve_client(:1947) → NativeOAISession(:1399)
           → 被 NativeToolClient(:1841) 包成统一 client
agentmain.GenericAgent.__init__(agentmain.py:68) → load_llm_sessions(:94) → self.llmclient
agentmain main --func(:309) → put_task(:149) → run(:188) 消费
run → agent_runner_loop(agent_loop.py:81) → client.chat(:99) [glm-5.1 真实往返]
    → 无 tool_calls(:109) → do_no_tool(ga.py:254) → StepOutcome
    → display_queue.put({'done': ...}) → 写 *.out.txt → break 退出
```

冒烟证据（`temp/smoke_prompt.out.txt`）：
- `[Info] Load mykeys from D:\GenericAgent\mykey.jsonc`
- `[Debug] Updated system prompt, length 4459 chars`
- `[Cache] input=3880 cached=0` / `[Output] tokens=6`
- 输出：`LLM Running (Turn 1) ... SMOKE_OK [ROUND END]`

---

## ① `agent_loop.py`（174 行，纯引擎，无业务）— 先读

**心智模型**：generator（非普通函数），把 "LLM 生成 → 工具 dispatch → 判停" 循环 yield 成流式 token，由上层 `agentmain.run()` 消费。

| 入口 | 行号 | 看什么 |
|---|---|---|
| `StepOutcome` | `:6-10` | 工具不直接控循环，只返回三元组：`data`(给 LLM 的结果) / `next_prompt`(下一轮喂什么，None=任务完成) / `should_exit`(硬退出) |
| `_TOOL_REGISTRY` + `register_tool`/`get_tool`/`discover_tools` | `:16-45` | **additive tool registry**（refactor-ga-extensibility 成果）。drop-in 工具 `tools/*.py` 用 `@register_tool` 自注册 |
| `BaseHandler.dispatch` | `:49-68` | **双轨 dispatch**：① 先找 `do_<name>` 方法（method-track）；② 没有则查 registry（registry-track）；③ 都没有 → `未知工具` outcome |
| `agent_runner_loop` | `:81-147` | **~60 行主循环**。每轮：`client.chat()` → 解析 `tool_calls` → 逐个 `handler.dispatch()` → 按 `StepOutcome.next_prompt` 判停。**关键约定 `:144`：history 存 Session 后端，messages 只传本轮新内容** |
| 三种 exit_reason | `:131-133,147` | `EXITED`(工具主动退) / `CURRENT_TASK_DONE`(next_prompt=None) / `MAX_TURNS_EXCEEDED`(轮数耗尽) |
| `_hook` 钩子点 | `:4-5,53,89,97-107,143,146` | 8 个钩子：tool_before/after、turn_before/after、llm_before/after、agent_before/after。**hermes 自进化挂在 `agent_after`** |

### 待确认疑点（深入时再看）
`do_no_tool` 返回的 `next_prompt` 是回复内容本身（非空），按 `agent_loop.py:132-133` 的 `if not outcome.next_prompt: break` 逻辑**不该** break。但冒烟测试确实在 1 轮后退出（`[ROUND END]`）。猜测：`do_no_tool` 的 `next_prompt` 可能实为 None，或走的是 `no_tool` 的特殊分支（`:116` `if tool_name == 'no_tool': pass`）。读 `ga.py:254` 的 `do_no_tool` 实现可解。

---

## ② `agentmain.py`（247 行，SDK + 4 模式）— 再读

| 入口 | 行号 | 看什么 |
|---|---|---|
| 启动期自注册 | `:12-16` | `discover_and_load()`（插件 hooks）+ `discover_tools(tools/)`（drop-in 工具）—— 两条注册链 import 期跑完 |
| `load_tool_schema` | `:25-30` | schema 从 `assets/tools_schema{,_cn}.json` 加载；glm/kimi/minimax 用 `_cn` 版（中文工具描述） |
| `get_system_prompt` | `:48-62` | sys_prompt = 模板 + 日期 + L2 全局记忆 + **MCP 工具摘要注入**(L1)。解释了冒烟时 `4459 chars` 来源 |
| `GenericAgent.__init__` | `:68-92` | task_queue/display_queue 模型 + MCP 单例启动 |
| `GenericAgent.run` | `:188-244` | **消费循环**：取 `task_queue` → `agent_runner_loop` generator → chunk 塞 `display_queue`。`:223` `consume_file('_stop')` 是外部中止信号 |
| `put_task` | `:149-152` | 所有前端唯一入口 |
| 4 模式分派 | `:268-337` | `--func`(读文件→跑→写 .out.txt→break) / `--task`(多轮、写 `temp/<IODIR>/`、等 `reply.txt`) / `--reflect`(监控脚本) / 无参=交互。**冒烟走 `--func` 路径（`:309-323`）** |
| 后台 spawn | `:268-279` | 不带 `--nobg` 会 self-spawn 成后台进程 → 冒烟测试必须加 `--nobg` |

---

## ③ `ga.py`（GenericAgentHandler，工具实现层）

| 入口 | 行号 | 看什么 |
|---|---|---|
| `class GenericAgentHandler(BaseHandler)` | `ga.py:27` | 继承 `agent_loop.BaseHandler`，提供 `do_*` 方法 |
| 9 原子工具 | `:49-254` | `code_run` / `ask_user` / `web_scan` / `web_execute_js` / `file_patch` / `file_write` / `file_read` / `update_working_checkpoint` / `get_skill_detail` |
| `do_mcp_call` | `:232` | MCP 统一调用入口（不管连多少 server 都走这一个工具） |
| `do_no_tool` | `:254` | LLM 没调工具时的占位 |
| `do_start_long_term_update` | `:304` | **自进化触发器**：任务完成后结晶 Skill（hermes v1 入口） |
| `get_global_memory` | `:398` | 拼 L2 全局记忆供 sys_prompt |
| utils | module-level | 已抽到 `ga_utils.py`（filetools/exectools/webtools/misc），经 named import + `__all__` re-export，`from ga import smart_format` 仍兼容 |

> `skill_manage` 工具**不在这里**——在 `tools/skill_manage.py`，走 registry-track（dual-track 第二条轨，Task 4 迁移成果）。

---

## ④ `llmcore.py`（LLM 多协议 Session 层，~1950 行）

| 入口 | 行号 | 看什么 |
|---|---|---|
| `_load_mykeys` | `:54-85` | 加载链：`mykey.py` → `mykey.jsonc` → `mykey.json`（冒烟走 jsonc 回退，`:69`） |
| `reload_mykeys` | `:92` | 带变更检测的 reload |
| `resolve_client` | `:1947` | 把 mykey cfg 解析成 Session 实例 |
| Session 类层级 | `:965-1841` | `BaseSession` → `ClaudeSession`/`LLMSession`(传统) / `NativeClaudeSession`/`NativeOAISession`(原生 tool) → `MixinSession`(故障转移) → 被 `ToolClient`/`NativeToolClient` 包成统一 client |
| 当前配置 | — | `NativeOAISession/openai` = glm-5.1 经 bbgate 网关，OAI 兼容协议 |

### Session 类层级图
```
BaseSession (965)
├─ ClaudeSession (1118, 传统) ─┐
├─ LLMSession (1166, 传统)    ─┤
├─ NativeClaudeSession (1236) ─┤→ MixinSession (1697, 故障转移，串多个)
└─ NativeOAISession (1399)    ─┘   └→ ToolClient(1458) / NativeToolClient(1841) 包成 client
```

---

## 配套命令

```bash
# 冒烟测试（已验证 PASS）
.venv\Scripts\python.exe -m agentmain --list-llms                                    # 零风险验 key 加载
.venv\Scripts\python.exe -m agentmain --func temp\smoke_prompt.txt --nobg            # 非交互单轮，写 .out.txt
cat temp\smoke_prompt.out.txt                                                        # 看输出

# 交互式 TUI（你来玩，看不到你画面）
.venv\Scripts\python.exe frontends\tui_v3.py
# Windows TUI 抖动 → 用 Git Bash，或 pip install -U prompt_toolkit rich
```

---

## 当前仓库状态快照（2026-07-19）

- 分支 `dev`，领先 `main` 99 提交，工作树干净，与 origin/dev 同步。
- 刚完成 `refactor-ga-extensibility` comet change（**已归档，verify PASS 7/7，199 pytest green**）—— additive tool-dispatch registry 重构。
- 5 个活跃 comet changes：`fix-skill-loader-home-windows`(hotfix,build)、`skill-lazy-load-mtime-cache`(full,build)、`add-first-class-subagents`(full,build)、`add-llm-slash-cmd`(tweak,archive)、`add-selfextract-installer`(full,**open** — 禁写源码)。
- hermes v1 已实现；v1.5 未开始（`hermes/NOTES_v1.5_recommendations.md` 有 R1-R9，R6 是真 bug）。
- `.agents/skills/` 不存在 → 仓库内未结晶过任何 Skill，自进化空状态。

## 约定
- Python 3.11/3.12（**禁 3.14**，与 pywebview 不兼容）· `uv` · `ruff check .`（E/F/W/I/UP/B len120 E501 忽略；存在历史违规）· `pytest tests/`
- 严格 **comet workflow + openspec/spec-superflow**，phase-guard：写源码前必查 `.comet.yaml` 的 phase。
