---
comet_change: add-durable-agent-protocol
role: technical-design
canonical_spec: openspec
archived-with: 2026-07-19-add-durable-agent-protocol
status: final
---

# Design Doc: Durable Agent Protocol v1

> 对应 OpenSpec change `add-durable-agent-protocol`。本文是 design.md 高层框架的深度技术细化：消息 schema、bridge 集成架构、线程模型、错误韧性、测试策略。不重写 proposal/spec；spec 12 需求 + S1-S9/E1-E3 是上游事实源。
>
> brainstorm 决策依据见 `openspec/changes/add-durable-agent-protocol/.comet/handoff/brainstorm-summary.md`。

## 1. 目标与边界

冻结 5 年耐久的 agent 协议 v1，作非 Python 前端（Rust CLI/TUI/GUI，后续 `add-rust-frontends` change）驱动 GA 的稳定契约面。引擎内部（agent_loop/ga.py/llmcore/hermes）可自由演化，只要不破协议契约。

**不改**：`agent_loop.py` / `ga.py` / `llmcore.py` 引擎内部、`--task`/`--func` 文件协议（coexist 不动）、reflect 脚本控制（不暴露到协议）。

**只读复用**：`GenericAgent` SDK（`put_task` / `display_queue` / `abort` / `next_llm` / `list_llms` / `_handle_slash_cmd`）、`plugins.hooks`（register/trigger）、`slash_cmds.prompt_for`、`goal_mode` 的 continuation 模式（思路复刻，不 shell out）。

## 2. 8 项设计决策（brainstorm 确认）

| # | 决策 | 选定 |
|---|---|---|
| Q1 | 版本化 | LSP 式：wire `version:"1"`（major only）+ `capabilities` 数组协商；破坏性变更 bump major |
| Q2 | 前向兼容 | 同 major 内新 server MUST 服务老 client；能力废弃 ≥1 minor 标 deprecated 仍可用，下个 major 才移除 |
| Q3 | tool 事件来源 | hook 路径：`tool_before`→`tool/call`，`turn_after`→`tool/result`（不动 agent_loop.py） |
| Q4 | bridge 落点 | 新模块 `ga_stdio.py`（镜像 ga_httpapp 先例），`agentmain.__main__` 不动 |
| Q5 | autonomous budget | `{seconds?, turns?}`（至少一，先耗尽者终止）；对齐 goal_mode 唯一真实实现；token 留 v2 |
| Q6 | slash 转发 | 专用 `slash/cmd{cmd,args}` 消息；注入类走 `prompt_for()`，状态类 raw 转发 |
| Q7 | 契约测试 | 一 spec 需求一黑盒 wire 测试，断结构（type 序列/必填字段/终态 reason）非内容 |
| Q8 | 多会话并发 | per-task GA 实例池 + 有界并发（默认 max 4）；hook 经 `self.parent` 反查 task_id 路由 |

## 3. 传输与帧

- 子进程 + stdio：Rust UI 作父进程 spawn `python -m ga_stdio` 为 child。
- 帧格式：newline-delimited JSON，每行一个 JSON 对象，UTF-8。
- 每条消息必含 `id`（client 分配，server 响应/事件带同 id 关联；server 主动事件用新 id）、`type`、`version`。
- stderr 留给 bridge 诊断日志（不参与协议）。
- 仅 stdout 承载协议消息；stdin 仅收 client 消息。

## 4. 能力协商

v1 capability 标识（字符串集合）：

```
streaming          # task/delta 流式
multi-session      # 多 task_id 并发
autonomous         # task/start{mode:autonomous,budget}
approval           # approval/request + approval/response 人机回路
mcp                # mcp/list 可见性
slash              # slash/cmd 转发
llm-switch         # llm/list + llm/select
session-resume     # session/resume
```

```
C→S  initialize {id, type:"initialize", version:"1", capabilities:[...client 端需要的能力...]}
S→C  ready       {id, type:"ready", version:"1", capabilities:[...server 支持的能力...], agent_info:{name:"GenericAgent", mcp_connected:N, llm_count:N}}
```

- client 在 `initialize.capabilities` 声明它依赖的能力；server 在 `ready.capabilities` 声明它支持的能力。
- 协商：client 依赖 ∖ server 支持 ≠ ∅ → server 回 `error{code:"capability_unsupported", missing:[...]}` 优雅退出（不崩）。
- 未发 `initialize` 直接发业务消息 → `error{code:"not_initialized"}` 拒绝执行（E3）。
- `ready` 之后才允许业务消息。

## 5. 消息 schema（每 type 字段）

> 字段约定：`id`/`type`/`version` 每条必有，下表省略。`?` 表示可选。`task_id` 由 server 在 `task/ack` 分配并回带，此后该 task 所有事件都带同一 `task_id`。

| 方向 | type | 字段 | 说明 |
|---|---|---|---|
| C→S | `initialize` | `capabilities[]` | client 依赖能力 |
| S→C | `ready` | `capabilities[], agent_info` | server 支持能力 + agent 元信息 |
| C→S | `task/start` | `prompt, mode?:"single"\|"autonomous", budget?:{seconds?,turns?}, images?[]` | 起任务；mode 默认 single |
| S→C | `task/ack` | `task_id, status:"running"\|"queued"` | 立即回 task_id；超并发上限→queued |
| S→C | `task/delta` | `task_id, content, turn?` | 流式增量；来自 display_queue 的 next |
| S→C | `tool/call` | `tool_id, task_id, name, args` | 来自 tool_before hook |
| S→C | `tool/result` | `tool_id, task_id, content` | 来自 turn_after hook 的 tool_results |
| S→C | `task/done` | `task_id, reason, turn?, error?` | reason: completed/budget/interrupted/error |
| C→S | `task/interrupt` | `task_id` | 中止运行中任务 → task/done{reason:interrupted} |
| S→C | `approval/request` | `task_id, tool_id, prompt, options?` | ask_user 触发，阻塞 agent 线程 |
| C→S | `approval/response` | `task_id, tool_id, decision:"approve"\|"reject", input?` | client 响应，唤醒 agent |
| C→S | `llm/list` | — | 列模型 |
| S→C | `llm/list` | `llms:[{no,name,current}], current_no` | 桥接 agent.list_llms() |
| C→S | `llm/select` | `n` | 切模型，桥接 agent.next_llm(n) |
| C→S | `session/resume` | `history[], llm_no?` | 恢复 llmclient.backend.history 续聊 |
| C→S | `slash/cmd` | `cmd, args` | slash 转发 |
| S→C | `slash/result` | `task_id?, injected_prompt?` | 注入类回注入的 prompt 摘要 + 续 task_id；状态类回结果 |
| C→S | `mcp/list` | — | 查 MCP 可见性 |
| S→C | `mcp/list` | `servers:[{name, tools:[...]}]` | 经 MCPClientManager.get_all_tools_summary() |
| S→C | `error` | `code, message, original_id?` | 通用错误；original_id 关联触发消息 |

`reason` 枚举：
- `completed` — 任务正常完成（next_prompt=None 或 EXITED）
- `budget` — autonomous 模式 budget 耗尽（收口轮后）
- `interrupted` — 被 task/interrupt 中止
- `error` — 引擎异常（agentmain.run 的 except 分支）

## 6. Bridge 集成架构

### 6.1 落点

新模块 `ga_stdio.py`：
```python
from agentmain import GenericAgent as GA  # 引擎不动
from plugins.hooks import register         # hook 注册（既有扩展点）
from frontends.slash_cmds import prompt_for # 纯函数，零 TUI 依赖
```
入口 `python -m ga_stdio`（`__main__` block）。`agentmain.__main__` 的 4 模式分派不动。

### 6.2 GA 实例池（Q8）

```
BridgeCore
  ├─ pool: {task_id: TaskCtx}  # TaskCtx = {ga: GenericAgent, dq: display_queue, thread, mode, budget, ...}
  ├─ semaphore: 有界并发（默认 max=4，env GA_STDIO_MAX_CONCURRENCY 可配）
  ├─ 每实例: ga.run() daemon 线程 ← consumes ga.task_queue → ga.display_queue
  └─ hook 回调（全局 _registry，与 langfuse/skill_evolution 等共存）
```

- `task/start`：分配新 task_id；若并发未满 → 起 GA 实例 + run 线程 + put_task → `task/ack{status:"running"}`；已满 → task_id 入队 + `task/ack{status:"queued"}`，有实例释放时按 FIFO 起跑。
- 每 task_id 独立 GA 实例 → 独立 history/llmclient → 真并发流式，不串台。
- 实例释放：task/done 后该 TaskCtx 的 GA 调 shutdown()（停 MCP），从 pool 移除，唤醒下一个 queued。

### 6.3 事件源与 hook 路由（Q3）

hook 全局注册一次（bridge 启动期），回调用 `ctx['self'].parent`（GA 实例）→ pool 反查 task_id：

- `tool_before`：`ctx = {self, tool_name, args, response, index, tool_num, ...}` → `self.parent` 是 GA 实例 → 查 task_id → 发 `tool/call{tool_id, task_id, name=tool_name, args}`。
  - 特例：`tool_name == "ask_user"` → 不直接发 tool/call，转 approval 流程（6.4）。
- `turn_after`：`ctx = {response, tool_calls, tool_results, turn, next_prompt, exit_reason, ...}` → 遍历 `tool_results`（`[{tool_use_id, content}]`）逐条发 `tool/result{tool_id=tool_use_id, task_id, content}`。
- `display_queue` 排空（每 task 独立线程）：item 形状 `{next|done, source, turn, outputs}`（agentmain.py:229-235）：
  - `next` → `task/delta{task_id, content=item['next'] 或增量, turn}`
  - `done` → 发剩余 `task/delta`（若 inc_out 尾巴）→ `task/done{task_id, reason=completed|error, turn}`

> 注：hook `ctx` 是 `locals()` 快照，字段随 agent_loop 版本可能微调。bridge 回调用 `.get(key)` 容错取值，不硬依赖字段全集——这是 hook 路径的韧性边界。build 阶段需对 agent_loop 各 hook 点字段做表驱动测试。

### 6.4 approval 人机回路（线程模型）

`ask_user` 工具被 LLM 调用时，agent_loop 走 `dispatch('ask_user', ...)` → 触发 `tool_before` hook。

- bridge 的 tool_before 回调检测 `tool_name == "ask_user"`：
  1. 发 `approval/request{task_id, tool_id, prompt=args['query']|args, options?}`。
  2. **阻塞当前 agent 线程**（该 GA 的 run 线程）等一个 `threading.Event`。
  3. stdio reader 线程（独立）收到对应 `approval/response` → 唤醒 Event → 回调把 `input` 注入为 ask_user 的返回值（替换原 dispatch 行为）。
- 关键：hook 是同步的，阻塞 tool_before = 阻塞该 GA 的 agent 线程；stdio reader 必须独立线程，不能被任何 agent 线程阻塞。
- `decision:"reject"` → ask_user 返回拒绝语义，agent 自行决定（如退出或换路径）。
- approval 超时（可配，默认无超时或长超时）→ 回 `error` + ask_user 返回超时语义。

> 实现注：tool_before 拦截 ask_user 后，原 dispatch 会继续调 `do_ask_user`。bridge 需让拦截的 input 成为 do_ask_user 的返回——要么 override handler 的 do_ask_user（bridge owns handler？不，handler 由 agentmain.run 内部创建），要么在 tool_before 回调里把 input 塞进 `args`/ctx 让 do_ask_user 读。build 阶段需核实 do_ask_user 的入参语义，定最干净的注入点（候选：tool_before 回调改写 `args['query']` 为预填响应，或 hook 返回 dict 替换 ctx）。这是 build 阶段的一个实现 spike。

### 6.5 autonomous 生命周期（Q5）

`task/start{mode:"autonomous", budget:{seconds?, turns?}}`：
- bridge 起一个 GA 实例 + 一个 **bridge-owned 自续循环**（不复用 reflect/goal_mode.py，D5）：
  ```
  start_time = now; turns_used = 0
  loop:
    if (now - start_time >= seconds) or (turns_used >= turns): 
      喂收口 prompt → 等 done → 发 task/done{reason:budget}; break
    喂 continuation prompt → put_task → 排空 display_queue 发 task/delta
    turns_used += 1
  ```
- continuation prompt 复刻 goal_mode 的 `CONTINUATION_PROMPT` 模式（objective/elapsed/remaining/turn + 检验/改进阶段规则），bridge 内 owns 一份（不从 reflect import，避免 reflect 控制面泄漏）。
- objective 来自 `task/start.prompt`（client 首轮 prompt 即 objective）。
- `task/interrupt` → 对应 GA `agent.abort()` → run 线程 stop_sig → 自续循环检测中断 → `task/done{reason:interrupted}`。
- budget 两者皆给时先耗尽者胜；至少给一个，都没给 → `error{code:"bad_budget"}`。

### 6.6 slash 转发（Q6）

`slash/cmd{cmd, args}`：
- `cmd` ∈ {/update, /autorun, /morphling, /goal, /hive, /conductor}（注入类）→ `injected = prompt_for(cmd, args)`；若 `injected is None`（非已知注入命令）→ 查是否状态类。
- `cmd` ∈ {/llm, /session.*, /resume}（状态类）→ raw `put_task(cmd + " " + args)`，让 `agentmain._handle_slash_cmd` 内部处理（改 agent 状态后 display_queue 直接发 done 系统消息）。
- `/scheduler` → 非协议范围 → `error{code:"slash_unsupported"}`。
- 注入类回 `slash/result{task_id, injected_prompt=injected摘要}` 并把注入 prompt 作为新 task 跑（复用 task 生命周期）。

### 6.7 child 崩溃韧性（E1）

- bridge 自身崩溃 → child 进程退出 → client 检测 stdout EOF（readline 返回 None / BrokenPipe）→ client 本地产 `error{code:"child_crashed"}` 事件，不阻塞等待。
- bridge 内部异常（单 task 引擎报错）→ agentmain.run 的 except 分支已把 error 折进 display_queue done → bridge 发 `task/done{reason:error, error}`，不崩进程。

## 7. 错误与韧性

| 场景 | 行为 |
|---|---|
| 畸形 JSON 行（E2） | 回 `error{code:"bad_json", original_id:null}`，不断链，继续读下一行 |
| 未初始化发业务消息（E3） | `error{code:"not_initialized"}`，拒绝执行 |
| 能力不匹配 | `error{code:"capability_unsupported", missing:[...]}`，优雅退出 |
| child 进程退出（E1） | client 检测 stdout EOF → 本地 `error{code:"child_crashed"}`，不挂死 |
| 单 task 引擎异常 | `task/done{reason:error, error}`，进程不崩，其他 task 不受影响 |
| 未知 type | `error{code:"unknown_type"}`，不断链 |
| id 不匹配的 approval/response | `error{code:"stale_approval"}`，忽略 |

## 8. 测试策略（Q7）

一 spec 需求一黑盒 wire 测试（`tests/test_protocol_*.py`，11 个，见 tasks.md §4）：

- **黑盒**：spawn `python -m ga_stdio` 子进程，stdin 发 JSON 行，stdout 读 JSON 行断言。
- **断结构非内容**：消息 type 序列、必填字段存在、终态 `reason` 正确；不断言 LLM 输出文本（非确定）。
- **autonomous**：极小 budget（`seconds=2` 或 `turns=1`）快速耗尽 → 断 `reason:budget`；再断 `task/interrupt` → `reason:interrupted`。
- **并发（S2）**：双 `task/start`，断 task_id 不串混、各 delta/done 归位。
- **边界**：E1（kill -9 child，断 client 不挂死）、E2（发非法 JSON 行，断 error 不断链）、E3（不发 initialize 直接 task/start，断 protocol error）。
- **hook 字段表**：build 阶段对 agent_loop 各 hook 点的 ctx 字段做表驱动测试，防 hook 回调因字段微调而脆。
- 现有 199 pytest 不动，新增 wire 测试补协议盲区。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| hook ctx 字段随 agent_loop 演化微调 | 回调用 `.get(key)` 容错 + hook 字段表驱动测试 |
| approval 阻塞 agent 线程的并发死锁 | stdio reader 独立线程；每 task 独立 GA 实例（互不阻塞） |
| v1 最大面冻结大，5 年改任一能力要升 v2 | LSP 版本化 + caps 协商 + 前向兼容窗口（已定 Q1/Q2） |
| stdio 点对点不支持多 Rust UI 连一 child | 接受；远程多客户端留未来 WS 变体（非目标） |
| per-task GA 实例资源（每实例连 MCP/LLM） | 有界并发 max 4 + done 后 shutdown 释放 |
| token budget 缺失 | v1 只 time+turns（对齐 goal_mode）；token 留 v2 经 llm hooks 加 |
| ask_user 拦截注入点 | build 阶段 spike 核实 do_ask_user 入参，定最干净注入 |

## 10. 实现顺序提示（build 阶段细化）

1. `ga_stdio.py` 骨架：stdio 读写循环 + initialize/ready 握手 + 畸形 JSON error（E2/E3 先过）。
2. 单 task 流式：put_task → display_queue 排空 → task/delta + task/done（S1）。
3. hook 注册：tool_before→tool/call、turn_after→tool/result（含 ctx 字段表）。
4. task/interrupt → abort → task/done{interrupted}（S3）。
5. approval 拦截 ask_user + 线程模型（S4）—— spike do_ask_user 注入点。
6. per-task GA 池 + 有界并发（S2）。
7. autonomous 自续循环 + budget（S7）。
8. slash/cmd 转发（S8/S9）。
9. llm/list/select + session/resume（S6）。
10. mcp/list（S5）。
11. 契约测试 11 个逐个补齐。

## 11. Spec Patch

无。本设计不回写 `specs/agent-protocol/spec.md`——schema 细节、bridge 架构、线程模型均属 Design Doc 内容；spec 12 需求 + S1-S9/E1-E3 场景 requirement 级已足够。

