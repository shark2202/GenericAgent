## Why

GenericAgent 当前所有前端都是 Python，直接 `from agentmain import GenericAgent` 进程内调用。非 Python 前端（即将新建的 Rust CLI/TUI/GUI）无法 import Python 类，需要一个稳定契约面来驱动 GA。现有 `--task`/`--func` 文件协议是 `agentmain.py` 顺手写的实现偶然——无版本号、无 schema 声明、无兼容承诺，扛不住 5 年演化。需要一个为耐久设计的协议 v1，让 Rust 及未来其它消费者锁一个不破的契约，引擎内部（agent_loop / ga.py / hermes）可自由演化，只要不破契约。

## What Changes

- **新增** agent 协议 v1：子进程 + stdio JSON-RPC（每行一个 JSON 对象，带 `id`/`type`/`version`），Rust 起 Python child 为 server，stdin/stdout 双向流式。
- **新增** v1 能力面：`initialize`/`ready` capability 协商 · `task/start`（含 `mode:autonomous` + `budget`）· `task/delta`（流式 token）· `tool/call` · `tool/result` · `task/done`（含 `reason:budget|interrupt`）· `task/interrupt` · `llm/list` · `llm/select` · `session/resume` · 多会话并发（`task_id`）· approval 人机回路 · MCP 工具可见性查询 · slash 命令转发（`/goal` `/hive` `/morphling` `/conductor` `/update` `/autorun`）。
- **新增** Python stdio 参考实现（bridge）：复用 `GenericAgent.put_task` / `display_queue`，落点（新模块 vs 扩展 `agentmain --stdio`）留 design。
- **新增** 契约测试（`tests/`）：覆盖 v1 能力面（现有 199 pytest 不覆盖任何 wire/CLI 协议面，是盲区）。
- **不改** `agent_loop.py` / `ga.py` / `llmcore.py` 引擎内部。
- **不移除** `--task`/`--func` 文件协议（与新协议 coexist，留 Python 内部用）。

## Capabilities

### New Capabilities

- `agent-protocol`: GA 对非 Python 前端暴露的 5 年耐久 agent 协议 v1。涵盖 stdio JSON-RPC 传输、任务生命周期（单任务 + autonomous 自续）、流式 / 工具事件 / 中断、多会话并发、approval 人机回路、LLM 切换、会话恢复、MCP 可见性、slash 命令转发。

### Modified Capabilities

<!-- 无。协议只读 MCP / skill / tool-dispatch，不改其 spec 级需求。-->

（无）

## Impact

- **新增代码**：Python stdio bridge（落点待 design）+ `tests/` 契约测试。新模块或 `agentmain` 扩展，不改引擎核心。
- **API**：新协议是 `GenericAgent` SDK 之上的薄契约层；SDK 的 `put_task` / `display_queue` / `abort` / `next_llm` / `list_llms` / `_handle_slash_cmd` 是协议内部依赖，不变。
- **依赖**：无新外部依赖（Python 端用 stdio + json；Rust 端在后续 `add-rust-frontends` change）。
- **现有件复用**：`assets/ga_httpapp.py`（put_task/display_queue 排空参考）、`frontends/slash_cmds.py`（slash 注入逻辑参考）、`reflect/goal_mode.py`（autonomous 自续参考）、`frontends/conductor.py`（反面参考——编排 schema 过重）。
- **流程**：build 阶段写 `tests/*.py` + Python bridge 前，需先推进/收尾 `add-selfextract-installer`（其 open 阶段禁写源码）。
