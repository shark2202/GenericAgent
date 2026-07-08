# GenericAgent-Go 产品需求文档 (PRD)

> 状态：实现至 Phase 5 · 日期：2026-07-02
> 源项目：GenericAgent (Python, ~3K LOC seed, 9 atomic tools, ~100-line loop)
> 目标：用 Go 重写核心引擎，保留 self-evolving 特性，换取类型安全 / 原生并发 / 单二进制部署
> 进度：Phase 0–5 完成（包结构/契约/Mock 闭环/周期注入/4 模式/9 工具/web 桥接/OAI+JSONC mykey/Python 分发）；L1 单元 27 test 全 pass；T5 self-evolving L1（Mock）达标。**L2 集成（真 LLM）已验证**：glm-5.2@bbgate 全闭环（tool_use→code_run→result 回传→终答）。L2 浏览器待测。

---

## 1. 概述

### 1.1 背景

GenericAgent 是一个极简、自我进化的自主 agent 框架。设计哲学：**不预载技能，进化技能**。每解决一个新任务，自动把执行路径结晶为可复用 Skill，形成从 3K 行种子代码长出的个人技能树。

Python 版核心由四个文件构成：

| 文件 | 行数级 | 职责 |
|------|--------|------|
| `agentmain.py` | ~250 | `GenericAgent` 类、入口、4 种运行模式 |
| `agent_loop.py` | ~180 | `agent_runner_loop`（generator 驱动）、`BaseHandler`、`StepOutcome` |
| `llmcore.py` | ~900 | 多 LLM 后端、MixinSession 混合路由、ToolClient 协议降级 |
| `ga.py` | ~600 | `GenericAgentHandler`（9 工具实现）、`code_run`、memory 注入 |

### 1.2 目标

用 Go 重写核心引擎，**不丢失**以下能力：

1. 9 原子工具 + ~100 行 agent loop 的极简内核
2. self-evolving：运行时生成代码 → 执行验证 → 沉淀为 L3 技能
3. L0–L4 分层 memory + Action-Verified 公理 + 周期注入防漂移
4. 多 LLM 后端（Claude / OAI 原生 tool use + 协议降级 + MixinSession 混合路由）
5. 4 种运行模式（CLI / task / func / reflect）

并换取：

- **静态类型**：`StepOutcome`、tool schema、message 结构有编译期保障
- **原生并发**：`goroutine + channel` 替代 `threading + Queue`
- **单二进制部署**：消除 Python 环境/依赖地狱
- **崩溃隔离**：生成的代码跑在子进程，不拖垮主进程

### 1.3 范围

**本次重写**：agentmain / agent_loop / llmcore / ga 四个核心模块 + memory 文件机制 + assets 资源加载。

**不重写（语言无关，直接复用）**：
- `memory/` 下所有 `.md` / `.py` / `.txt`（纯文本，Go 直接读写）
- `assets/sys_prompt*.txt`、`assets/tools_schema*.json`、`assets/insight_fixed_structure*.txt`、`assets/global_mem_insight_template*.txt`
- `frontends/`（IM bot 前端，后续按需单独迁移）
- `plugins/`（hooks 机制用 Go interface 注册重做，见 §3.4）

### 1.4 非目标

- 不追求与 Python 版逐行等价
- 不内嵌 CPython 解释器（cgo + GIL + ABI 风险过大，详见 §8.2）
- 不重写 L3 现有 SOP/脚本内容
- 第一版不做分布式 / 多 agent 编排

---

## 2. 现有架构分析（Python 源）

### 2.1 核心数据流

```
用户 query
   │
   ▼
GenericAgent.run()  ── task_queue 拉取 ──> _handle_slash_cmd
   │
   ▼
get_system_prompt()  = sys_prompt.txt + Today + get_global_memory()  ← L1 注入
   │
   ▼
GenericAgentHandler(parent, history, cwd)  ── 继承旧 handler 的 key_info/passed_sessions
   │
   ▼
agent_runner_loop(client, sys_prompt, query, handler, TOOLS_SCHEMA, max_turns=180)
   │  (generator, yield chunk)
   │
   ▼ per turn:
   ┌─────────────────────────────────────────────┐
   │ client.chat(messages, tools)  ── ToolClient  │
   │   ├─ _build_protocol_prompt  (协议降级)       │
   │   ├─ backend.ask  (Claude/OAI/Mixin 流式)     │
   │   └─ _parse_mixed_response  (<tool_use> 解析) │
   ├─────────────────────────────────────────────┤
   │ handler.dispatch(tool_name, args)            │
   │   └─ do_<tool_name>  (generator, yield log)  │
   ├─────────────────────────────────────────────┤
   │ handler.turn_end_callback                    │
   │   ├─ 提取 <summary> 入 history_info           │
   │   ├─ turn%7/10/25/175 周期注入                │
   │   └─ 注入 master intervene/keyinfo            │
   └─────────────────────────────────────────────┘
   │
   ▼
messages = [{role:user, content:next_prompt, tool_results}]
   │  (history 保留在 backend 内部，每轮只发增量)
   ▼
display_queue.put({done: full_resp})  ── 回传调用方
```

### 2.2 LLM 后端层（llmcore.py）

- `BaseSession`：基类，持 cfg/history/model/name，`ask(prompt)` generator
- `ClaudeSession` / `LLMSession`：文本协议（`<tool_use>` 块）
- `NativeClaudeSession` / `NativeOAISession`：原生 tool use（function calling）
- `MixinSession`：多模型混合，按 `mixin_cfg` 主从路由（主模型跑，失败转备）
- `ToolClient`：包装 backend，提供
  - `chat(messages, tools)`：统一 generator 接口，返回 `MockResponse{content, tool_calls}`
  - **协议降级**：`_build_protocol_prompt` 把 tools schema 注入 prompt，`<tool_use>` 文本块格式，`_parse_mixed_response` 解析——兼容不支持原生 function calling 的模型
  - `last_tools` 缓存：tools schema 未变时不重复发送，省 token
  - `compress_history_tags` / `trim_messages_history`：历史压缩

### 2.3 工具层（ga.py）

9 个原子工具，均实现为 `do_<name>` generator 方法：

| 工具 | 核心 | Go 化难点 |
|------|------|----------|
| `code_run` | subprocess 跑 python/bash，流式 stdout | 低（exec.Command） |
| `file_read` | 读文件 + 行号 | 无 |
| `file_patch` | 精确字符串替换 | 无（strings.Replace） |
| `file_write` | overwrite/append/prepend + `{{file:}}` 展开 | 无 |
| `web_scan` | 简化 HTML + 标签页 | 中（依赖 TMWebDriver/CDP） |
| `web_execute_js` | 浏览器执行 JS | 中（依赖 TMWebDriver/CDP） |
| `update_working_checkpoint` | 写 working memory dict | 无 |
| `ask_user` | 阻塞等用户输入 | 低（channel） |
| `start_long_term_update` | 触发 LLM 按 L0 SOP 蒸馏记忆 | 无（纯 prompt） |

### 2.4 运行模式（agentmain.py `__main__`）

| 模式 | 触发 | 行为 |
|------|------|------|
| CLI | 默认 | `input()` 循环，`inc_out` 增量打印 |
| task | `--task DIR` | IO 文件驱动：读 input.txt → 写 output.txt → 等 reply.txt 多轮 |
| func | `--func FILE` | 读 prompt 文件 → 写 .out.txt → 退出（无多轮） |
| reflect | `--reflect SCRIPT` | 加载监控脚本，`check()` 触发任务，支持热重载 + on_done |

---

## 3. Go 版架构设计

### 3.1 设计原则

1. **记忆文件零迁移**：L0–L4 全是纯文本，Go 直接读写，格式不变。
2. **Go 做静态骨架，Python 做动态肉身**：Go 写 loop/工具/memory IO；运行时生成的技能脚本仍是 Python，靠 `code_run` 的 subprocess 边界衔接。
3. **generator → channel**：Python 的 `yield` 流式改为 `chan string` + `context.Context` 控制生命周期。
4. **崩溃隔离优先**：所有外部执行（code/shell/web）跑子进程或独立 goroutine，主进程不背锅。
5. **接口先行**：LLM 后端、工具、handler 全部面向 interface 编程，便于测试和扩展。
6. **core/frontend 边界抽象**：core 只依赖 `EventSink` 接口输出事件，不绑死通信方式。首版 A（同进程 `chan Event`），预留 B（跨进程 NDJSON over stdin/stdout）切换，切换时只换 sink 实现不动 core。

### 3.2 包结构

```
go-agents/
├── cmd/ga/              # main 入口，4 模式 flag 分发（--task/--func/--reflect/--input/--llm）
├── internal/
│   ├── agent/           # 核心类型契约：Event/EventSink/Tool/ToolEvent/Session/Message/ToolCall/Response
│   ├── app/             # GenericAgent 主体 + 4 运行模式（RunCLI/RunTask/RunFunc/RunReflect）
│   ├── loop/            # RunLoop + Handler（Dispatch 注入 args["_handler"]/TurnEnd/GetAnchorPrompt）
│   ├── llm/             # ToolClient（协议降级）+ MockSession + OaiSession + mykey(JSONC)
│   ├── tools/           # 9 原子工具（无状态，args 注入 handler）+ web Bridge
│   ├── memory/          # InitMemory/GetGlobalMemory/LogMemoryAccess
│   ├── prompt/          # sys_prompt 加载
│   └── python/          # Resolve(root) Python 解析器（5 级优先级，分发捆绑/系统）
├── scripts/bundle-python.sh  # python-build-standalone 捆绑（4 arch，自包含分发）
└── PRD.md
# assets/ + memory/ 在 GA_ROOT（父目录 = Python 版根），Go 直接读写，零迁移
```

### 3.3 核心类型（接口级示意）

```go
// 一个工具调用的产出
type StepOutcome struct {
    Data       any
    NextPrompt string  // 空 = 当前任务完成
    ShouldExit bool
}

// 工具接口：对应 Python 的 do_<name> generator
type Tool interface {
    Name() string
    Dispatch(ctx context.Context, args map[string]any, resp Response) Stream[ToolEvent]
}

// ToolEvent：工具 dispatch 流的事件。Log=中间日志，Outcome=最终产出（nil=未结束）。
// 对应 Python generator 的 yield(日志) + return(StepOutcome)。
type ToolEvent struct {
    Log     string
    Outcome *StepOutcome
}

type Stream[T any] <-chan T  // 替代 Python generator，由 chan 驱动

// LLM 后端
type Session interface {
    Ask(ctx context.Context, prompt string) Stream[string]  // 流式文本 chunk
    History() []Message
    SetHistory([]Message)
}

// ToolClient：协议降级 + 统一返回
type ToolClient struct {
    backend    Session
    lastTools  string  // 缓存，省 token
    logPath    string
}
// Chat 返回 ChatItem 流：Chunk=流式文本，Final=末尾解析的 Response（对应 Python client.chat generator）
func (c *ToolClient) Chat(ctx context.Context, msgs []Message, tools []ToolSpec) <-chan ChatItem

// Response 统一结构（对应 Python MockResponse）
type Response struct {
    Content    string
    ToolCalls  []ToolCall
    StopReason string
}

// Event：core → frontend 的事件流（对应 Python display_queue 的 dict）
type Event interface{ event() }
type TurnStart struct{ Turn int }
type Chunk    struct{ Text string; Source string; Turn int }
type ToolLog  struct{ Text string }
type Done     struct{ FullResp string; Turn int; Err error }

// EventSink：core 对前端的输出抽象。A/B 两实现，core 只依赖此接口。
type EventSink interface {
    Emit(Event) // 投递（A: 写 chan；B: 写 NDJSON）
    Close()     // 任务结束
}

// A 阶段实现：同进程 channel，TUI 直接 range sink.Chan()。Emit 非阻塞（满则丢，不阻塞 loop）。
type ChanSink struct{ ch chan Event }
func (s *ChanSink) Chan() <-chan Event { return s.ch }

// B 阶段实现（后续）：跨进程 NDJSON over stdout
// type JSONLineSink struct{ w io.Writer }
```

### 3.4 关键改造点

#### 3.4.1 generator → channel

Python `agent_runner_loop` 是一个大 generator，`yield` 既输出 LLM 流式 chunk，又输出工具日志。Go 版拆成：

```go
// RunLoop 把事件写入 sink，调用方经 sink 消费。A: ChanSink；B: JSONLineSink。
// Event/EventSink 类型见 §3.3。
func RunLoop(ctx context.Context, sink EventSink, client *ToolClient, sysPrompt string,
    user string, handler *Handler, tools []ToolSpec, maxTurns int) error
```

调用方经 `sink` 消费事件（A 阶段 `range sink.Chan()`，B 阶段 sink 内部写 NDJSON），`ctx.Done()` 中止。handler.dispatch 的 generator 同理改成返回 `Stream[ToolEvent]`（Log + *StepOutcome）。

#### 3.4.2 插件 / hooks 动态加载

Python 用 `importlib` 运行时 `discover_and_load`。Go 静态语言做不到同形，改两条路：

- **编译期注册**：`plugins/` 下每个插件 `init()` 里调 `Register("name", hook)`，主程序 import 即生效。简单可靠，覆盖 90% 场景。
- **运行期脚本**：需要热加载的监控逻辑走 reflect 模式（外部脚本子进程），不进主二进制。

#### 3.4.3 code_run：Go 调 Python

详见 §4.3.1。核心：`exec.Command` subprocess，不内嵌解释器。

#### 3.4.4 core/frontend 边界：先 A 后 B

core 只依赖 `EventSink` 接口（§3.3），不绑死通信方式：

| 阶段 | sink 实现 | 进程关系 | 前端 |
|------|----------|---------|------|
| **A（首版）** | `ChanSink`（`chan Event`） | 同进程 | Go TUI (bubbletea) 直接 `range sink.Chan()` |
| **B（后续）** | `JSONLineSink`（NDJSON over stdout） | 跨进程 | 任意语言 TUI / 远程前端读 stdin |

输入侧同理：A 阶段 TUI 直接调 `agent.PutTask(ctx, query, source) EventSink`；B 阶段 core 起 `stdin → PutTask` 适配 goroutine，读 NDJSON 命令行。

**切换代价**：A→B 只需新增 `JSONLineSink` 实现 + stdin 适配 goroutine，core 的 `RunLoop`/`GenericAgent`/工具/memory 零改动——这是留抽象边界的全部意义。首版即应冻结 `Event` 的 JSON 字段名，避免 B 阶段 schema 断裂。

#### 3.4.5 工具无状态化 + args 注入

工具**不持 `*loop.Handler` 字段**（Python 版 `do_*` 是 handler 方法，天然持 self）。Go 版工具是无状态 struct，`Handler.Dispatch` 统一注入：

```go
args["_cwd"] = h.Cwd; args["_root"] = h.Root; args["_handler"] = h
args["_index"] = index; args["_tool_num"] = toolNum
```

工具从 `args["_handler"].(*loop.Handler)` 取 handler（Python 风格）。**原因**：`runOne` 每个 task 新建 handler，若工具持 handler 字段，工具注册时绑死的是旧 handler——跨任务 key_info 继承失效。无状态 + 运行时注入修复此 bug。

### 3.5 配置：mykey 加载（JSONC，纯 Go）

Python `reload_mykeys()` 从 `mykey.py` 动态 import。Go 版改读 **`mykey.jsonc`**（JSON + 注释，纯 Go 解析），**不走 python3 子进程**——mykey 实际是纯静态 dict 赋值（无 import/def/env/动态逻辑），JSONC 无损等价，且让 LLM 路径（mykey + OAI Session）完全脱离 python3/bundle。

- `LoadMykeys(root)`：读 `<root>/mykey.jsonc`（fallback `mykey.json`）→ `stripJSONC`（字符串安全状态机，剥 `//` + `/* */`，字符串内 `https://` 不被吃）→ `json.Unmarshal` 为 `map[string]map[string]any`。
- 模板：`mykey_template.jsonc`（GA root）。Go 已实现 `native_oai_config`；`native_claude_config`/`mixin_config` 注释待 key。
- 不支持尾逗号（避免字符串内 `,}` 误删）；`remote_url` 远端分发未实现（add when 需要中央 key 分发）。
- 原 §8.5 的 "python3 子进程 fallback" 方案**已废弃**——纯 Go JSONC 更简单，零 subprocess。

---

## 4. 功能需求

### 4.1 Agent 引擎

**F1.1** `GenericAgent` struct：持 `taskQueue chan Task`、`LLMClients []ToolClient`、`history []string`、`handler *Handler`、`isRunning/stopSig`、`logPath`。

**F1.2** `Run()` goroutine：循环消费 taskQueue，为每个 task 构造 `EventSink`（A 阶段 `ChanSink`），调 `RunLoop(ctx, sink, ...)`，事件经 sink 回传前端。`stopSig` 经 `ctx cancel` 中止。`PutTask(ctx, query, source) EventSink` 返回 sink 供前端消费。

**F1.3** LLM 会话加载：`LoadLLMSessions()` 从 mykey 配置构造 `[]ToolClient`，支持 `next_llm` 切换并迁移 history。

**F1.4** slash 命令：`/session.k=v` 设 backend 属性、`/resume` 恢复会话。

**F1.5** 长 prompt（>2000 字符）自动落盘为 temp 文件，query 替换为"读取该文件"指令。

### 4.2 LLM 后端层

**F2.1** `Session` 接口 + Claude / OAI 实现，SSE 流式解析（对应 `_parse_claude_sse` / `_parse_openai_sse`）。

**F2.2** `NativeClaudeSession` / `NativeOAISession`：原生 function calling（对应 `_parse_openai_json` + `_responses` API）。

**F2.3** `MixinSession`：多模型混合路由，主模型失败转备，配置驱动。

**F2.4** `ToolClient.Chat`：
- 协议降级：`BuildProtocolPrompt` 把 tools schema 注入 system prompt，`<tool_use>` 文本块格式
- `ParseMixedResponse`：解析 `<tool_use>` 块 + 原生 tool_calls，统一为 `Response`
- `lastTools` 缓存：schema 未变时只发"工具仍有效"提示，省 token
- `file_write` 特殊处理：args 里移除 `content`，改要求放 `<file_content>` 标签（防 args 爆炸）

**F2.5** 历史管理：`CompressHistoryTags`（旧标签压缩）、`TrimMessagesHistory`（按 token 成本轮询裁剪）。

**F2.6** 日志：每个 response 写 `temp/model_responses/model_responses_<logid>.txt`，支持 `/resume` 读取。

### 4.3 工具集（9 个）

#### 4.3.1 `code_run` —— 代码执行（核心，self-evolving 基石）

**需求**：执行 LLM 生成的 Python/Bash 代码，流式回传 stdout，支持超时和中断。

**实现（A 档 subprocess，首版）**：
```go
func CodeRun(ctx context.Context, code, lang string, timeout time.Duration, cwd string) (string, error)
// lang=python: 写 .ai.py（注入 code_run_header.py 等价物）→ exec.Command("python3","-X","utf8","-u",f)
// lang=bash:   exec.Command("bash","-c",code)
// lang=go:     exec.Command("go","run",f)  (预留)
// stdout 流式：bufio.Scanner 读行，写回 Stream
// ctx.Done() 或 stopSignal → Kill 进程树
```

**C 档（常驻子进程 + stdio JSON 协议，可选增强）**：python 长驻进程，Go 经 stdin 发 `{code}`，读 stdout JSON 结果。延迟从 ~150ms 降到 ~1ms，且 python 侧 `_state` 跨调用保持。崩溃可重启。首版不做，A 档够用。

**inline_eval 等价物**：Python 版在 handler 进程内 `exec(code, ns)` 并注入 `handler`/`parent` 引用。Go 版**主动放弃**此模式（进程内 exec 拿主对象引用是危险设计），改为：常用辅助函数预置到 `code_run_header.py`，生成代码经文件 IO 而非对象引用获取上下文。

#### 4.3.2 `file_read`
读文件，1-based 行号，`start`/`count`/`show_linenos`，默认 200 行。读 `memory/` 或 `sop` 时调 `LogMemoryAccess` 并注入"按 SOP 执行请提取关键点"提示。

#### 4.3.3 `file_patch`
精确字符串替换（`old_content` 必须唯一匹配）。支持 `{{file:path:start:end}}` 引用展开。失败提示 `file_read` 复查。**memory 文件强制走 patch，禁止 overwrite**（对应 L0 公理）。

#### 4.3.4 `file_write`
`overwrite`/`append`/`prepend` 三模式。`{{file:}}` 引用展开。仅用于大块新建，小改用 `file_patch`。

#### 4.3.5 `web_scan` / 4.3.6 `web_execute_js`
依赖 CDP（Chrome DevTools Protocol）驱动浏览器。Python 版经 `TMWebDriver` + `simphtml.py`。Go 版用现成 CDP 库（如 `chromedp`）重写，或保留 Python `TMWebDriver` 子进程 + JSON 协议桥接（首版更快）。**首版走桥接**，后续视情况原生 chromedp。

#### 4.3.7 `update_working_checkpoint`
写 `handler.working["key_info"|"related_sop"]`，重置 `passedSessions=0`。

#### 4.3.8 `ask_user`
阻塞等用户输入，`should_exit=true` 挂起当前任务。CLI 模式走 stdin，task/func 模式走 reply 文件。

#### 4.3.9 `start_long_term_update`
不直接写记忆。把 L0 SOP（`memory_management_sop.md`）内容 + `get_global_memory()` 作为 `next_prompt` 喂回 LLM，让 LLM 自行判断分层 + `file_patch` 最小化写入。**Action-Verified 闭环在此体现**：只有 `code_run` 验证成功的结论才允许进 L2/L3。

### 4.4 Memory 机制

**F4.1 L0–L4 文件分层**（格式与 Python 版完全一致）：

| 层 | 文件 | Go 处理 |
|----|------|---------|
| L0 | `memory/memory_management_sop.md` | `os.ReadFile` |
| L1 | `memory/global_mem_insight.txt` (≤30 行索引) | `os.ReadFile` |
| L2 | `memory/global_mem.txt` (事实库) | `os.ReadFile` |
| L3 | `memory/*.md` `*.py` (SOP + 脚本) | 读写，脚本经 code_run 执行 |
| L4 | `memory/L4_raw_sessions/` | reflect 模式自动收集 |

**F4.2 `GetGlobalMemory()`**：拼 `cwd` + `insight_fixed_structure.txt` + L1 内容，注入 system prompt。每轮 `turn%10==0` 重新注入防漂移。

**F4.3 周期注入**（`turn_end_callback`）：

| 轮次 | 注入 |
|------|------|
| `%7==0` | 提醒调 `update_working_checkpoint` |
| `%10==0` | 重注入 `GetGlobalMemory()` |
| `%25==0` | 提醒写文件持久化 checkpoint |
| `%175==0` | 强制 `ask_user` 汇报，禁盲重试 |

**F4.4 工作记忆跨任务继承**：新 handler 从旧 handler 继承 `key_info` + `passedSessions`，`passedSessions>0` 时注入"这是 N 个对话前的 key_info，先更新或清除"。

**F4.5 `LogMemoryAccess`**：读 `memory/` 或 `sop` 文件时更新 `memory/file_access_stats.json`（count + last date），供频率归层评估。

**F4.6 四公理**（prompt 层约束，语言无关）：Action-Verified / Sanctity / No-Volatile-State / Minimum-Sufficient-Pointer。原样保留在 L0 SOP 文本。

### 4.5 运行模式

**F5.1 CLI**：`bufio.Scanner(os.Stdin)` 读输入，`PutTask` 返回 `ChanSink`，`range sink.Chan()` 增量打印（`inc_out` 等价），Ctrl-C 中止当前任务。A 阶段同进程；B 阶段切 `JSONLineSink` 后，CLI 退化为 core daemon + 最小 stdin 命令循环。

**F5.2 task**：`--task DIR`，IO 文件驱动，input.txt → output.txt，等 reply.txt 多轮，`_stop` 文件中止。后台化用 `nohup` 等价（首版不内嵌 daemon，靠 shell）。

**F5.3 func**：`--func FILE`，读 prompt → 写 `.out.txt` → 退出。

**F5.4 reflect**：`--reflect SCRIPT`。Go 不动态 import 脚本，改为**子进程协议**：reflect 脚本作为独立进程（Python 或 Go 二进制），经 stdout 输出 JSON `{task}`，Go 主进程轮询消费。`check()` 失败重试、`on_done` 回调经 stdin 下发结果。热重载 = 重启子进程。`ONCE`/`INTERVAL` 经协议字段传递。

---

## 5. 非功能需求

| 项 | 要求 |
|----|------|
| **Go 版本** | ≥1.26（go.mod 实测） |
| **平台** | macOS / Linux / Windows 三端，纯 Go 无 cgo |
| **依赖** | 最小化：标准库优先；CDP 用 `chromedp`（可选）；SSE 自写或轻量库 |
| **外部依赖** | code_run/web/reflect 经 `python.Resolve` 解析（优先捆绑 `python/`，fallback 系统 python3）；web 工具另需浏览器 + TMWebDriver master |
| **配置** | `mykey.jsonc`（JSON+注释，纯 Go 解析），模板 `mykey_template.jsonc` |
| **Python 分发** | `scripts/bundle-python.sh` 捆绑 python-build-standalone（PBS 20260510 + Py 3.11.15，4 arch）；`internal/python/Resolve(root)` 5 级优先级（GA_PYTHON → exe 同侧 → GA root bundle → cwd bundle → 系统 python3）。mac-x64 实测 207M deps OK |
| **日志** | `slog` 结构化，替代 `print`；model_responses 日志格式兼容 Python 版（`/resume` 共用） |
| **并发安全** | `GenericAgent` 单实例串行处理任务（queue 天然串行），多实例独立 |
| **性能** | 单轮 LLM + 工具往返不引入额外延迟；code_run A 档 ~150ms 启动开销可接受 |
| **资源** | memory 索引 L1 ≤30 行硬约束不变 |

---

## 6. 迁移策略（分阶段）

### Phase 0 — 骨架与契约 ✅
- 包结构、`Session`/`Tool`/`Handler`/`StepOutcome`/`Event`/`EventSink` 接口定义
- `assets/` + `memory/` 复用（GA_ROOT 父目录，零迁移）
- `GetGlobalMemory` + sys_prompt 注入跑通

### Phase 1 — 单后端 MVP ✅
- `ToolClient` 协议降级（`BuildProtocolPrompt` + `ParseMixedResponse`）+ `MockSession`
- `RunLoop` channel 版跑通
- 4 工具：`code_run`(python) / `file_read` / `file_patch` / `file_write`（无状态化，`args["_handler"]` 注入）
- CLI 端到端跑通（Mock 驱动）

### Phase 2 — 完整工具 + 周期注入 ✅
- 补齐 `update_working_checkpoint` / `ask_user` / `start_long_term_update`
- 周期注入全量（turn%7/10/25/175）+ working memory 闭环（anchor prompt + FoldEarlier）
- `LogMemoryAccess` + `file_access_stats.json`

### Phase 3 — 运行模式 + self-evolving 闭环 ✅
- 4 模式：CLI / task（input→output→reply 多轮）/ func（prompt→.out）/ reflect（子进程协议 `python3 -c` 调 check()/on_done()，热重载=mtime）
- 跨任务 key_info 继承 + passed_sessions 递增（对应 agentmain.py:215-220）
- self-evolving 闭环 demo（Mock：code_run 验证 → file_write L3 → start_long_term_update 蒸馏）

### Phase 4 — web 工具桥接 ✅
- `web_scan` / `web_execute_js` 经持久子进程 `assets/tmwebdriver_bridge.py`（NDJSON 协议，复刻 ga.web_scan/web_execute_js/smart_format）
- 9/9 原子工具全齐

### Phase 5 — OAI Session + mykey + Python 分发 ✅
- `OaiSession`：纯 Go `net/http` + SSE，复刻 `_openai_stream`(chat_completions) + `_stream_with_retry` + `auto_make_url` + 指数退避；gpt-5/o 系列用 `max_completion_tokens`
- mykey 改 **JSONC 纯 Go**（`mykey.jsonc` + `stripJSONC`），LLM 路径脱离 python3
- Python 分发：`scripts/bundle-python.sh`（python-build-standalone）+ `internal/python/Resolve` 5 级解析器，4 调用点（coderun/web/mode×2）全接入
- **关键 bug 修复**：RunLoop 的 `tc.ID != ""` 门丢光所有文本协议 tool_call 的 `Data`（模型看不到 code_run 输出/file_read 内容）→ 对齐 Python `agent_loop.py:95` 无条件回传（`tc.Name != "no_tool"`）；`stringify` 类型感知（string 原样，map JSON 不转义 HTML）
- T5 self-evolving L1（Mock）达标：跨 run 写 L3 → 读 L3 → code_run 复用 → 输出回传

### Phase 6 — 打磨（持续，待真 key / 浏览器）
- code_run C 档常驻子进程（按需）
- chromedp 原生替换桥接（按需）
- fsnotify 热加载 mykey
- 性能 profiling + bundle 瘦身（删 pycache/docs，目标 <100M）
- Claude/Mixin Session 骨架（待 key）
- T1–T4/T6–T8 验收用例 + reflect(T7) 端到端 demo

---

## 7. 验收基准与方法

> **实现状态（2026-07-02）**：L1 单元层 27 test 全 pass（app 2 + loop 7[5 单元+2 e2e] + llm 7 + python 2 + tools 7 + tui 2），vet 无警告。T5 self-evolving L1（Mock）达标。**L2 集成（真 LLM）已验证**：bbgate 网关 + glm-5.2，func + TUI 双模式跑通 tool_use→code_run→tool_result 回传→终答全闭环（PRD §6 Phase 5 的 tool_result 回传 bug 修复在真模型下确认成立）。L2 浏览器（web_scan/web_execute_js）待 TMWebDriver master + 扩展连接。

### 7.1 验收基准（Baseline）

**基准源**：Python 版是 ground truth。Go 版验收 = 与 Python 版行为等价 + 性能不劣化 + 新增优势兑现。Python 版本身无单元测试，靠 self-bootstrap（README：“Everything was completed autonomously by GenericAgent”），其内置验收方法见 `memory/subagent.md`「场景1：测试模式 - 行为验证」：**只给目标不诱导，验证自主完成 + 自主导航 SOP**。Go 版沿用此哲学。

**非确定性处理**：LLM 输出不可逐字对比。验收三件事，不验文本：
1. 工具调用序列是否合理（轨迹对比）
2. 文件副作用（memory/temp 产物）是否一致
3. 任务最终成功与否

确定性逻辑（工具函数 / memory 读写 / 协议降级解析 / `<tool_use>` 解析）用 Mock LLM 驱动，逐字节对比。

### 7.2 验收分层

| 层 | 驱动 | 对比对象 | pass 线 |
|----|------|---------|--------|
| L1 单元 | Mock LLM（固定响应） | Python 工具函数输出 | 字节级一致 |
| L2 集成 | 真 LLM（temp=0，同 model） | Python 版同任务轨迹 | 任务完成 + 副作用语义等价 |
| L3 系统 | 真 LLM + 真环境 | Python 版能力清单 | 全部用例 pass |

### 7.3 验收用例集（标准任务）

借用 subagent 测试模式（只给目标不诱导）：

| ID | 用例 | 验收点 | 对应验收项 |
|----|------|--------|-----------|
| T1 | 文件操作：temp 下创建/读取/patch 一个文件 | file_write/read/patch 字节级等价 | §7.4-1 |
| T2 | 代码生成执行：生成脚本计算斐波那契并验证 | code_run stdout + 退出码一致 | §7.4-2 |
| T3 | 记忆更新：发现一个事实，patch 进 L2，同步 L1 | memory 双向兼容 | §7.4-3 |
| T4 | SOP 导航：只给目标，从 L1 insight 自主找到正确 SOP | 导航能力等价 | §7.4-1 |
| T5 | self-evolving：生成工具脚本→code_run 验证→存 L3→复用 | 闭环跑通 | §7.4-2 |
| T6 | task 多轮：input.txt→reply.txt 往返 | task 模式等价 | §7.4-5 |
| T7 | reflect：监控脚本触发任务 | reflect 子进程协议 | §7.4-5 |
| T8 | 崩溃隔离：code_run 跑 segfault 脚本 | 主进程存活 | §7.4-6 |

### 7.4 验收项与 pass 线

1. **CLI 端到端**：Go 版用同一份 `memory/`+`assets/`，T1+T2+T3+T4 全 pass。基准：Python 版同任务同 LLM(temp=0) 能完成。
2. **self-evolving 闭环**：T5 跑通——生成的脚本 code_run 成功 → file_patch 存 L3 → 下次任务 file_read 引用并执行成功。
3. **memory 双向兼容**：Go 写 `global_mem.txt`/`global_mem_insight.txt`，Python 版 `get_global_memory()` 读取并注入 prompt 无异常；反向亦然。字节级或语义等价 pass。
4. **多后端**：OAI + Claude 各跑通 T1；MixinSession 主模型人为失败（断网/key 错）→ 备模型接管成功。
5. **4 模式**：CLI(T1) / task(T6) / func(读 prompt 写 out 退出) / reflect(T7) 各 pass。
6. **崩溃隔离**：T8 `code_run` 执行 `import os; os._exit(1)` / segfault 脚本，Go 主进程存活且能继续接受新任务。
7. **单二进制**：`go build` 产单文件；干净机器（仅装 python3 + 浏览器）跑通 T1。
8. **`/resume`**：Go 版读 Python 版 `model_responses` 日志，恢复 history 并继续对话。
9. **EventSink 抽象**：`RunLoop` 仅依赖 `EventSink`；`ChanSink`(A) 跑通 T1；新增 `JSONLineSink`(B) 后 core 代码零改动跑通 T1（用 stub TUI 读 NDJSON）。
10. **性能不劣化**：单轮 LLM 往返延迟 ≤ Python 版 ×1.5（含 code_run A 档 ~150ms 启动）；memory 注入耗时 ≤ Python 版。

### 7.5 验收流程与准入

1. **Phase 1 准入**：L1 单元全 pass（Mock LLM）+ T1+T2 端到端。
2. **Phase 2 准入**：T1–T4 + 多后端 pass。
3. **Phase 3 准入（发版基线）**：T1–T8 全 pass + 单二进制 + 崩溃隔离 + memory 双向兼容。
4. 每次验收：固定 LLM model + temp=0 + 同一份 memory 快照，Go/Python 各跑 3 轮取通过率；任一用例 3 轮全 pass 方算该用例 pass。

---

## 8. 风险与取舍

### 8.1 generator → channel 的语义损耗
Python `yield` 在单个调用栈里既流式输出又控制流，Go 拆成 channel 后跨 goroutine，调试栈不连续。**缓解**：`RunLoop` 内部用 `chan Event` 聚合，单 goroutine 驱动，保持逻辑单线程化；`context.WithCancel` 统一中止。

### 8.2 不内嵌 CPython（已决策）
cgo 绑 libpython 有五重坑：跨 GC 边界引用计数、GIL 串行化、ABI 平台差异、崩溃不隔离、库停更。**决策**：code_run 走 subprocess（A 档）或常驻子进程+协议（C 档），生成代码跑子进程，主进程零 cgo。代价：丢失 `inline_eval` 进程内 exec + 主对象引用，但该模式本身是危险设计，subprocess 化是改进。

### 8.3 web 工具依赖
Python 版 web 工具强依赖 `TMWebDriver` + CDP bridge + `simphtml.py`，逻辑重。**决策**：首版 Go 主进程经子进程桥接 Python `TMWebDriver`，快速复用；后续按需用 `chromedp` 原生重写。风险：桥接引入 Python 依赖，削弱"单二进制"目标——但 web 工具非核心路径，可标注为可选 extras。

### 8.4 reflect 模式动态性
Python reflect 直接 `importlib` 加载脚本调 `check()`。Go 改子进程协议后，热重载 = 重启子进程，`on_done` 回调经 stdin 下发。**代价**：reflect 脚本需符合协议格式，不能任意 Python 函数。**缓解**：提供 `reflect_wrapper.py` 适配层，旧脚本包一层即兼容。

### 8.5 mykey.py → mykey.jsonc 迁移（已改纯 Go JSONC）
原方案（python3 子进程 `import mykey` 导出 JSON）**已废弃**。实测 mykey.py 顶层全是静态 dict 赋值（无 import/def/env/动态逻辑），JSONC 无损等价。**决策**：纯 Go 读 `mykey.jsonc`（`stripJSONC` 字符串安全剥注释），LLM 路径（mykey + OAI Session）完全脱离 python3/bundle。代价：失去 `remote_url` 远端 key 分发与任意 Python 动态计算（边缘，add when 需要中央分发）。用户从 mykey.py 迁移 = 一次性拷值到 mykey.jsonc（模板 `mykey_template.jsonc`）。

### 8.6 SOP 文本的语言绑定
L3 部分 SOP 是中文且引用 Python 具体路径。Go 版 cwd / temp 路径需与 Python 版一致（`script_dir/temp`），否则 SOP 里的相对路径失效。**决策**：保持 `temp/` 在二进制同侧目录，与 Python 版布局一致。

### 8.7 A→B 切换（core/frontend 解耦）
首版 A（同进程 `ChanSink`）绑定 Go TUI，前端必须 Go 写。切 B（`JSONLineSink` 跨进程 NDJSON）后前端可任意语言/远程。**缓解**：core 只依赖 `EventSink` 接口（§3.4.4），A→B 只换 sink 实现 + 加 stdin 适配 goroutine，core 零改动。风险点：B 阶段 NDJSON schema 要稳定且向后兼容，否则前端升级断裂——首版即冻结 `Event` 的 JSON 字段名。

---

## 附录 A：Python 源文件 → Go 包映射

| Python | Go 包 | 说明 |
|--------|-------|------|
| `agentmain.py` | `cmd/ga` + `internal/app` | 入口 + GenericAgent 主体 |
| `agent_loop.py` | `internal/loop` + `internal/agent` | RunLoop + Handler/StepOutcome（类型契约在 agent） |
| `llmcore.py` | `internal/llm` | ToolClient + MockSession + OaiSession + mykey(JSONC) |
| `ga.py` | `internal/tools` + `internal/memory` | 9 工具（无状态）+ GetGlobalMemory/LogMemoryAccess |
| `simphtml.py` / `TMWebDriver.py` | `assets/tmwebdriver_bridge.py`（子进程桥接，Go 侧 `internal/tools/web.go`） | HTML 简化 + CDP 驱动 |
| `assets/*` | GA_ROOT `assets/`（复用，零迁移） | 纯文本资源 |
| `memory/*` | GA_ROOT `memory/`（复用，零迁移） | 纯文本记忆 |
| —（新增） | `internal/python` | `Resolve(root)` Python 解析器（捆绑/系统） |
| —（新增） | `scripts/bundle-python.sh` | python-build-standalone 捆绑分发 |

## 附录 B：9 工具 schema（与 Python 版一致，复用 `tools_schema.json`）

`code_run` / `file_read` / `file_patch` / `file_write` / `web_scan` / `web_execute_js` / `update_working_checkpoint` / `ask_user` / `start_long_term_update`

（schema JSON 直接复用 `assets/tools_schema*.json`，Go 仅做加载 + 平台分支：非 Windows 把 `powershell` 替换为 `bash`，与 Python `load_tool_schema` 行为一致。）
