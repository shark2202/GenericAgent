## ADDED Requirements

### Requirement: 单二进制 Rust 引擎
引擎 SHALL 编译为单个 Rust 二进制 `ga-engine`，不内嵌 CPython 解释器、不依赖系统 Python/uv/编译器。分发体积 SHALL 显著小于 Python PBS bundle（目标 <10MB vs 现 149MB archive/96MB installer）。

#### Scenario: 单二进制独立运行
- **WHEN** 在无 Python 环境的系统上运行 `ga-engine`（作为 durable protocol 的 Python child 等价物）
- **THEN** 进程启动、完成 initialize/ready 握手、处理业务消息，不因缺 Python 而失败

### Requirement: durable protocol v1 契约遵循
Rust 引擎 SHALL 实现已归档 `add-durable-agent-protocol` 冻结的 durable protocol v1（stdio newline-delimited JSON，id/type/version），经 11 wire 测试（§4.1-4.11）对齐 Python `ga_stdio.py` 行为。

#### Scenario: 11 wire 测试对齐 Python
- **WHEN** Rust 引擎跑 durable protocol 的 11 个 wire 测试
- **THEN** 全部通过，行为与 Python `ga_stdio.py` 等价（握手/握手韧性/单任务流式/多会话/中断/approval/MCP/llm-session/autonomous/slash/resilience）

### Requirement: tokio async + agent_loop channel 重构
引擎 SHALL 用 tokio async runtime。agent_loop SHALL 用 `mpsc::Sender<AgentEvent>` 推事件替代 Python 生成器 yield。AgentEvent enum SHALL 只含 `TurnStart{turn}` 与 `TextChunk(String)` 两变体（对应 Python `agent_runner_loop` 的两类 yield 值）。display buffer 聚合（DisplayUpdate/Done）SHALL 在 engine 层非 agent_loop 层。

#### Scenario: agent_loop 事件流式
- **WHEN** Rust agent_loop 运行单任务
- **THEN** 经 channel 推出 TurnStart + TextChunk 序列，engine 层聚合成 task/delta + task/done，对齐 Python 流式

### Requirement: llmcore Native-only + 手撸 HTTP
引擎 SHALL 只实现 NativeClaudeSession/NativeOAISession/MixinSession（砍传统 ClaudeSession/LLMSession 类）。SSE 解析（`_parse_claude_sse`/`_parse_openai_sse`）SHALL 完整迁移（Native 在用，非死代码）。HTTP SHALL 手撸 reqwest（非 SDK），保 GA 自定义 beta header 与伪装 payload。

#### Scenario: SSE 分片重组正确性
- **WHEN** Claude/OAI 原生 tool-use SSE 含 input_json_delta/tool_calls 分片到达
- **THEN** Rust parser 重组出完整 ToolUse（input JSON 字符串拼接后整体 parse），与 Python `_parse_claude_sse`/`_parse_openai_sse` 产出等价（golden snapshot 验证）

#### Scenario: 自定义 beta header 保留
- **WHEN** Rust NativeClaudeSession 构造请求
- **THEN** 含 16+ 伪装 header（claude-cli UA 等）、anthropic-beta 7 项 + 条件 [1m]、?beta=true、sk-ant- 判定 x-api-key vs authorization、context_management 硬编码、metadata.user_id 紧凑 JSON、fake_cc_system_prompt、cache_control markers，与 Python llmcore.py:1268-1340 语义等价

### Requirement: MixinSession 故障转移与 spring-back
MixinSession SHALL 实现多 session 故障转移 + spring-back：故障（error chunk "!!!Error:"/"[Error:"）切下一个 session；成功从备用恢复后锁 cur_idx；超过 spring_sec（默认 300s）切回 primary。SHALL 支持跨 NativeClaude+NativeOai 混用（同 Native 组，tools schema 各 session 内部转）。

#### Scenario: spring-back 时机
- **WHEN** MixinSession 当前在备用 session（cur_idx≠0）且经过 spring_sec
- **THEN** 下次 pick() 切回 primary（cur_idx=0）

#### Scenario: 跨协议混用
- **WHEN** MixinSession 组合 NativeClaudeSession + NativeOAISession
- **THEN** tools 广播时各 session 收到对应 schema（Claude→input_schema，OAI→functions），故障转移跨协议切换

### Requirement: 工具分层（EngineCap + ExternalTool）
引擎 SHALL 分两层工具：EngineCap（持引擎句柄，调内部 API）+ ExternalTool（无句柄，含 MCP）。dispatch SHALL EngineCap 优先 → ExternalTool → 未知工具。inline_eval（进程内 eval 摸引擎对象）SHALL 移除，由 EngineCap 显式工具（register_done_hook/enter_plan_mode/get_history/get_memory_state）等价替代。

#### Scenario: inline_eval SOP 改写后工具等价
- **WHEN** Rust 引擎处理依赖 inline_eval 的 SOP（autonomous_operation_sop 的 done_hook 注册 / plan_sop 的 enter_plan_mode）
- **THEN** 经 EngineCap 工具 register_done_hook/enter_plan_mode 等价达成，不依赖进程内 eval

#### Scenario: queued interrupt（I1）
- **WHEN** 对 queued task（未占 semaphore 槽位，ctx.ga is None）发 task/interrupt
- **THEN** 移出 _queued + pool，发 task/done{reason:interrupted}，不调 abort、不 semaphore.release，无僵尸 hand-off

### Requirement: hermes 自进化（distill worker + Scorer trait）
引擎 SHALL 实现 post-task skill distillation：agent_loop 完成后经 channel 推 DistillJob（history 快照 + catalog）给常驻 distill worker，worker 用独立 session 跑 LLM 蒸馏 + Scorer trait 打分闸门 + skill_manage 落盘。SHALL 不阻塞 agent_loop。熔断 MAX_AUTO_PATCH_PER_SKILL=5。

#### Scenario: distill worker 非阻塞
- **WHEN** agent_loop 完成 post-task 触发 distill
- **THEN** 推 DistillJob 到 channel 后立即返回（不等 distill 完成），主循环不被拖累；distill worker 独立消费

#### Scenario: Scorer 闸门与熔断
- **WHEN** distill 产出 skill op 经 Scorer 打分
- **THEN** score>=60 且 verdict=pass 才落盘；单技能连续 auto patch 达 5 次后只产 Brief 不再 patch

### Requirement: L1-L4 内存文本文件复刻
引擎 SHALL 原样复刻 L1-L4 内存机制（文本文件 + prompt 拼接）：L1=global_mem_insight.txt/L2=global_mem.txt/L3=memory/*.md/L4=L4_raw_sessions/。与 Python 版 SHALL 双向兼容（同一 memory/ 目录可被两版读写）。memory/ 里的 .py 工具脚本 SHALL 经 code_run(subprocess) 间接调用，环境由用户准备，引擎不内嵌 Python。

#### Scenario: 内存目录双向兼容
- **WHEN** Rust 引擎与 Python 版共享同一 memory/ 目录
- **THEN** Rust 版读写的 L1/L2/L3 文件 Python 版可正确解析，反之亦然
