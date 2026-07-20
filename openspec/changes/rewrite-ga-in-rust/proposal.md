## Why

当前 GA 引擎全 Python，分发必须捆 PBS 可移植 CPython 解释器（archive 149MB / installer 96MB），且 `assemble-dist-local.sh` 逐文件 cp 在 Windows + 杀毒实时扫描下慢到 ~50 分钟级（已验证回归）。根因是"引擎本身是 Python"——只要引擎是 Python，分发就绑不开 CPython runtime + 依赖树。

本 change 把 GA 引擎（agent_loop / llmcore / ga 工具 / mcp_client / ga_stdio 协议 / skill / hermes 自进化 / L1-L4 内存）迁到 Rust，产出**单个 Rust 二进制（几 MB，无 Python runtime）**，根治分发复杂。Python 版双轨共存兜底（经刚冻结的 durable protocol v1 无感切换）。

## What Changes

- **新增** Rust 引擎二进制 `ga-engine`：tokio async runtime，实现 durable protocol v1（agent-protocol 契约）+ agent_loop（生成器→mpsc::Sender channel 重构）+ llmcore（仅 Native Session，手撸 reqwest，保 GA 自定义 beta header）+ MixinSession 故障转移 + per-task 有界并发池。
- **新增** 工具分层：引擎能力（register_done_hook / enter_plan_mode / get_history / get_memory_state / checkpoint / ask_user / skill_query）编译期注册 + 强类型 API；外部工具（file/code/web/mcp）Rust 原生实现。
- **BREAKING** 外部用户工具扩展模型变更：drop-in `tools/*.py` 自注册 → 统一为 MCP server（`tools/list`+`tools/call` 协议）。现有 `tools/skill_manage.py` 等 drop-in 工具改写为 MCP server（`skill_manage` 因调引擎内部 distill 回路，归引擎能力层编译期注册，不作为外部 MCP 工具）。
- **BREAKING** 砍 `inline_eval` 进程内 eval：`do_code_run` 的 inline 模式（LLM 写代码直接摸 `handler`/`parent`/`history` 引擎对象）移除。改用 subprocess code_run + 显式结构化工具（`register_done_hook`/`enter_plan_mode`/`get_history`/`get_memory_state`）等价替代。受影响的 2 处 SOP（`memory/autonomous_operation_sop.md` 的 `handler._done_hooks.append(...)`、`memory/plan_sop.md` 的 `handler.enter_plan_mode(...)`）改写为显式工具调用。
- **BREAKING** 砍传统 LLM Session：`ClaudeSession`/`LLMSession`（手写 SSE + 手写 tool 分片解析）整条路移除（mykey 模板已只配 Native，属死代码）。只迁 `NativeClaudeSession`/`NativeOAISession`/`MixinSession`（故障转移 + spring-back）。
- **保留** L1-L4 内存机制原样复刻：文本文件 + prompt 拼接（L1=`global_mem_insight.txt` / L2=`global_mem.txt` / L3=`memory/*.md` / L4=`L4_raw_sessions/`），Rust 版读写同一 memory 目录，与 Python 版内容兼容。`memory/` 里的 `.py` 工具脚本环境由用户准备，Rust 不内嵌 Python。
- **保留** hermes 自进化：distill worker task + channel（post-task 推 history 快照 + catalog，不阻塞主循环）+ `Scorer` trait（`InProcessScorer`/`SubagentScorer` 两 impl）+ 熔断 `MAX_AUTO_PATCH=5` 原样。
- **不改** Python 版源码（双轨共存，Python 版兜底）；**不做** Rust CLI/TUI/GUI 前端（前端经 durable protocol 不变）；**不动** `--task`/`--func` coexist。
- **新增** 双轨接管路线：Rust 版按 11 阶段逐能力对齐 Python 行为，每阶段用 durable protocol 的 wire 测试验证。MVP 发版边界 = 阶段 0-8（含 hermes + L1-L4 + skill + 核心引擎 + 并发 + MCP + approval）。autonomous（阶段 9）/ slash 转发（阶段 10）/ Python 退役（阶段 11）留 MVP 后。

## Capabilities

### New Capabilities
- `rust-engine`: GA 引擎的 Rust 原生实现。单二进制、tokio async、无 Python runtime。实现 durable protocol v1（conforms to `agent-protocol`）、分层工具（核心编译期 + 外部 MCP）、llmcore（仅 Native + MixinSession 故障转移）、hermes 自进化（distill worker + Scorer trait + 熔断）、L1-L4 内存（文本文件复刻）、per-task 有界并发。记录与 Python 版的 breaking 分歧（inline_eval 砍、传统 Session 砍、drop-in→MCP）。

### Modified Capabilities
- `tool-dispatch`: 外部工具注册模型变更。drop-in `tools/*.py` 自注册（method-track/registry-track dual dispatch）→ 外部用户工具统一为 MCP server（`tools/list`+`tools/call`），核心工具编译期注册。breaking：现有 drop-in `.py` 工具须改写为 MCP server（`skill_manage` 例外，归引擎内置）。

## Impact

- **新增代码**：Rust 引擎 crate（`ga-engine/` 或 `ga_rust/`，复用已存在的 `frontends/desktop/src-tauri/` Rust 痕迹），~5600 LOC 核心引擎 Rust 重写 + hermes/memory + 新建 skill/hermes wire 测试。依赖：`reqwest` + `tokio` + `rmcp`（MCP SDK）+ `serde`/`serde_yaml` + 基础 crates。无 Python runtime 依赖。
- **BREAKING 影响**：(1) 现有 `tools/*.py` drop-in 工具须改写为 MCP server；(2) mykey 配置若用传统 Session（ClaudeSession/LLMSession）须切 Native；(3) 依赖 `inline_eval` 的 SOP 须改写为显式工具调用（已识别 2 处，需全量扫）。
- **分发简化**：Rust 单二进制（几 MB）替代 PBS bundle（149MB archive / 96MB installer）。`assemble-dist-local.sh` 的逐文件 cp 痛点消失（无 14215 PBS 文件）。
- **双轨共存**：经 durable protocol v1，前端可无感在 Python 引擎与 Rust 引擎间切换。Python 版全程兜底，Rust 版逐能力对齐（wire 测试）后切默认。
- **依赖关系**：依赖 `add-durable-agent-protocol`（已归档）冻结的 durable protocol v1 契约 + 11 wire 测试作为对齐基座。不依赖 `add-selfextract-installer`（open）。
