---
comet_change: rewrite-ga-in-rust
role: technical-design
canonical_spec: openspec
---

# Design Doc: rewrite-ga-in-rust（GA 引擎 Rust 重构）

> 对应 OpenSpec change `rewrite-ga-in-rust`。本文是 design.md 高层框架的深度技术细化：Rust 模块切分、event/channel schema、SSE 解析结构、MixinSession 状态机、工具分层 trait、distill worker 编排、测试策略。13 项 grilling 决策见 `openspec/changes/rewrite-ga-in-rust/design.md`；brainstorm 决策依据见 `.comet/handoff/brainstorm-summary.md`。不重写 proposal/spec。

## 1. 目标与边界

把 GA 引擎从 Python 迁到 Rust，产出**单二进制（无 Python runtime）**，根治分发复杂（PBS bundle 149MB archive / 96MB installer / ~50min assemble）。MVP（阶段 0-8）功能等价 Python GA 核心 + 自进化。经 durable protocol v1 双轨共存，Python 版兜底。

**不改**：Python 版源码（双轨）、agent_loop/ga/llmcore 引擎语义（行为对齐，仅语言换）、--task/--func coexist。
**不做**：Rust CLI/TUI/GUI 前端（经协议不变）、autonomous/slash/Python 退役（MVP 后）。

## 2. 13 项决策（摘要，详见 design.md）

B 引擎迁移 / MVP=核心+L1-L4+hermes+MCP+skill / A 文本内存 / py 工具用户环境 / A+C 砍 inline_eval / B 工具分层 / D 核心编译期+外部subprocess / C+iii 外部=MCP / C Native-only（**砍传统 Session 类，SSE 解析保留**——设计发现 1 修正 Q9） / A 手撸 reqwest / A tokio async / B distill worker+channel+trait / B 双轨 / MVP=阶段0-8。

## 3. Crate 模块切分（深化项 1）

单 crate 多模块（`ga-engine` binary）。依赖自上而下，无环：

```
ga-engine (binary)
├─ main.rs                    # tokio runtime + BridgeCore 组装
├─ protocol/                   # durable protocol v1（最底层）
│  ├─ frame.rs                 # id/type/version JSON 帧（serde）
│  ├─ dispatch.rs             # 路由表
│  └─ handlers/                # initialize/task_start/task_interrupt/approval/slash/llm/session/mcp_list
├─ llmcore/
│  ├─ session.rs              # Session trait + BaseSession + cfg 字段全集
│  ├─ claude_native.rs        # NativeClaudeSession（16+ 伪装 header + beta + ?beta=true + context_management + metadata + fake_cc_system_prompt + cache_control + sk-ant- 判定）
│  ├─ oai_native.rs           # NativeOAISession（codex_exec 伪装 + responses/chat_completions 两 api_mode + Claude-via-OAI cache markers）
│  ├─ mixin.rs                # MixinSession 故障转移 + spring-back
│  ├─ sse.rs                  # 完整 SSE 解析（Claude 7 事件 + OAI 两 api_mode + 分片重组 + 多 JSON 切分）
│  └─ retry.rs                # _stream_with_retry 重试退避
├─ tools/
│  ├─ dispatch.rs             # EngineCap + ExternalTool 双表 dispatch
│  ├─ builtin/                # file_read/file_write/file_patch/code_run(subprocess)
│  ├─ engine_caps/            # register_done_hook/enter_plan_mode/get_history/get_memory_state/checkpoint/ask_user/skill_query/skill_manage
│  └─ mcp.rs                  # rmcp 客户端 + McpTool wrapper
├─ agent_loop.rs              # tokio task，mpsc 推 TurnStart/TextChunk
├─ engine.rs                  # GenericAgent 等价 + put_task + display buffer 聚合
├─ pool.rs                    # per-task 池 + tokio Semaphore + 槽位移交 + I1 fix
├─ skill/
│  ├─ loader.rs               # discover/get_detail/sync_to_l1（serde_yaml frontmatter + mtime）
│  └─ manage.rs               # skill_manage（EngineCap 内置）
├─ memory.rs                   # L1-L4 文件读写 + prompt 拼接 + L4 压缩
└─ hermes/
   ├─ distill.rs              # distill worker + channel
   └─ scorer.rs               # Scorer trait + InProcess/Subagent
```

**依赖方向**：`protocol ← llmcore ← tools ← agent_loop ← engine ← pool ← main`；`memory ← skill ← hermes`；hermes 与 agent_loop 经 channel 单向（agent_loop 推 DistillJob，hermes 消费，无回调）→ 无循环。

## 4. agent_loop event schema（深化项 2）

Python `agent_runner_loop` 只 yield 两类（已核实 agent_loop.py:81-147）：`{'turn':N}` 和 `str`。Rust：

```rust
enum AgentEvent {
    TurnStart { turn: u32 },
    TextChunk(String),              // LLM 流/工具头/工具输出/围栏/未知工具，不区分来源
}
```

agent_loop 薄（纯控制流，推这两类）。display buffer 聚合放 `engine.rs`（对应 agentmain.py:221-235 二级抽象）→ `DisplayUpdate{text,source,turn,recent_outputs}` / `Done{text,source,turn,outputs}` / `SlashDone{text,source}`。

## 5. llmcore SSE 解析（深化项 3，重头）

**SseEvent enum**：
```rust
enum SseEvent {
    Text(String),                              // text_delta 立即推（低延迟）
    Thinking { text: String, signature: String },
    ToolUse { id: String, name: String, input: Value },  // content_block_stop 重组完推
    Usage { cached_read: u32, cache_creation: u32, input: u32, output: u32 },
    Warn(String),                              // 流异常/max_tokens/错误
    Done { stop_reason: Option<String> },
}
```

**ClaudeSseParser**（对应 _parse_claude_sse，llmcore.py:255-361）：7 事件 message_start/content_block_start/content_block_delta/content_block_stop/message_delta/message_stop/error。content_block_delta 4 子类型：text_delta（yield）/thinking_delta（累积）/signature_delta（累积）/input_json_delta（partial_json 字符串拼接到 tool_json_buf）。content_block_stop 时 json.loads(tool_json_buf) 重组 ToolUse。异常收尾：未收 message_stop → warn 插入；max_tokens → truncation warn。

**OaiResponsesParser**（_parse_openai_sse responses 分支）：response.output_text.delta（yield）/response.function_call_arguments.delta（按 output_index 聚合拼接）/response.completed（usage）。

**OaiChatCompletionsParser**（chat_completions 分支）：delta.tool_calls[] 按 index 聚合 + delta.reasoning_content + delta.content。

**_try_parse_tool_args**：args `{..}{..}` 连写用 `(?<=\})(?=\{)` 切分多 dict。

**自定义 header/payload 清单（claude_native.rs/oai_native.rs，逐项复刻）**：
- Claude：16+ 伪装 header（claude-cli UA `claude-cli/2.1.152`/x-app=cli/X-Stainless-*/X-Claude-Code-Session-Id UUID4）；`anthropic-beta` 7 项（claude-code-20250219/interleaved-thinking-2025-05-14/redact-thinking-2026-02-12/context-management-2025-06-27/prompt-caching-scope-2026-01-05/effort-2025-11-24）+ 条件 `[1m]`→`context-1m-2025-08-07`（index 1 插入，去 model 后缀）；`?beta=true` query param；`sk-ant-` 判定 `x-api-key` vs `authorization: Bearer`；硬编码 `context_management.edits[0]={type:"clear_thinking_20251015",keep:"all"}`；`metadata.user_id` 紧凑 JSON；`fake_cc_system_prompt` 伪装 "You are Claude Code, Anthropic's official CLI for Claude."；最后 2 user block + tool 末项 + system list `cache_control:{"type":"ephemeral"}`；thinking{type:adaptive/enabled/disabled}+budget_tokens；output_config{effort}。
- OAI：伪装 codex_exec `codex_exec/0.139.0` UA + `originator` header；responses 模式 `prompt_cache_key`+`client_metadata`+`include:["reasoning.encrypted_content"]`；chat_completions `stream_options.include_usage` + model `gpt-5`/`o1-o4` 前缀→`max_completion_tokens`；Claude-via-OAI `_stamp_oai_cache_markers`（model 含 claude/anthropic 时给最后 2 user 加 cache_control）。

## 6. MixinSession 故障转移（深化项 4）

`MixinSession impl Session trait`，持 `Vec<Box<dyn Session>>`。显式委托/广播（broadcast attrs system/tools/temperature/max_tokens/reasoning_effort/history/stream/read_timeout 列成 trait 方法，tools 各 impl 内部转 schema）。

**状态机**（对应 llmcore.py:1697-1815）：
- `pick()`：cur_idx≠0 且 elapsed>spring_sec（默认 300s）→ 切回 primary(idx 0)。
- `raw_ask`：按 (base+attempt)%n 轮询。error chunk 过滤（"!!!Error:"/"[Error:" 开头，未 yield 时吞）。
- 成功 attempt>0 → 锁 cur_idx=idx, switched_at=now。
- 成功 attempt==0 但 last_chunk 含 "[!!! 流异常中断" → 部分失败，下次切 idx+1。
- 失败 → 下一个 attempt；整轮失败 time.sleep 指数退避（min 30s）。
- 跨 NativeClaude+NativeOai 混用（同 Native 组，mykey 模板支持），tools 广播按 session 类型转。

## 7. 工具分层 trait（深化项 5）

```rust
#[async_trait]
trait EngineCap: Send + Sync {          // 引擎能力，持 &Engine
    fn name(&self) -> &str;
    async fn call(&self, engine: &Engine, args: Value) -> ToolResult;
}
#[async_trait]
trait ExternalTool: Send + Sync {       // 外部工具，无引擎句柄
    fn name(&self) -> &str;
    fn schema(&self) -> ToolSchema;
    async fn call(&self, args: Value) -> ToolResult;
}
```

dispatch 分两表（EngineCap 优先 → ExternalTool → 未知工具 yield），对应 Python method-track 优先 registry。hook 两层（tool_before/tool_after）。EngineCap 持 `&Engine` 调内部：get_history/get_memory_state/enter_plan_mode/register_done_hook/skill_query/distill_trigger + skill_manage（调 skill_loader/distill，Q11 归引擎内置）。ExternalTool 含 `McpTool` wrapper（impl ExternalTool，call 走 rmcp tools/call，schema 从 rmcp tools/list 动态拿）。EngineCap 是 Q5 inline_eval 替代品（显式强类型工具 vs Python 裸 eval）。

## 8. distill worker + channel（深化项 6）

```rust
struct DistillJob {
    history_snapshot: Vec<Message>,   // 拷贝，非 live
    catalog: String,
    task_meta: TaskMeta,
}
struct DistillWorker {
    rx: mpsc::Receiver<DistillJob>,
    scorer: Box<dyn Scorer>,
    llm: Arc<dyn Session>,             // 独立 session
    skill_store: Arc<SkillStore>,
}
```

agent_loop 持 `mpsc::Sender<DistillJob>`（有界，背压）。post-task（agent_after 等价，turns>=SKILL_DISTILL_MIN_TURNS(6) && GA_SKILL_EVOLUTION_ENABLED 守卫在推前判）→ `tx.send(job).await`。worker 常驻 tokio task：rx.recv() → distill（独立 session 调 LLM 产 op）→ Scorer 闸门（parse_op→score→apply_op）→ skill_store 落盘（SkillWriteOrigin::BackgroundReview）。不阻塞 agent_loop，无回调，无循环依赖。

## 9. Scorer trait（深化项 7）

```rust
enum VerdictKind { Pass, Reject, Revise }
enum ScorerSource { InProcess, Subagent, Degraded }
struct Verdict { score: u8, verdict: VerdictKind, dims: Dims, rationale: String, source: ScorerSource }
struct Dims { reusability: u8, verifiedness: u8, non_redundancy: u8 }

#[async_trait]
trait Scorer: Send + Sync {
    async fn score(&self, op: &Op, skill_md: &str, history: &[Message], catalog: &str) -> Result<Verdict, ScorerDegraded>;
}
```

InProcessScorer（serde 强类型反序列化即校验，替代 Python runtime JSON schema）+ SubagentScorer（独立 LLM 调子 + 解析校验）。阈值闸门 GA_SKILL_SCORER_THRESHOLD(60) 在 parse_op/apply_op 间。熔断 MAX_AUTO_PATCH_PER_SKILL=5（单技能连续 auto patch 后只产 Brief）。

## 10. per-task 池 + 并发（深化项 8）

tokio::sync::Semaphore（max 4，env GA_STDIO_MAX_CONCURRENCY）+ `HashMap<task_id, TaskCtx>` + `VecDeque<DistillJob>` queued + `_release_task` 槽位移交（acquire 释放时 pop queued → spawn → task/ack{running}）。I1 queued-interrupt fix 保住：handle_task_interrupt 检 ctx.ga is None（queued）→ 移出 _queued+pool + done{interrupted}，不 abort 不 semaphore.release。

## 11. L1-L4 内存（深化项 9）

`memory.rs`：get_global_memory() 读 L1(global_mem_insight.txt)+L2(global_mem.txt)+L3 结构导航拼 prompt（对应 ga.py:407-419）；sync_skills_to_l1() 写回 L1；L4 会话归档压缩。`skill/loader.rs`：SKILL.md + serde_yaml frontmatter 扫描 + mtime 缓存 + get_skill_detail/backup_and_patch_skill。与 Python 版**双向兼容**（同一 memory/ 目录）。

## 12. 测试策略（深化项 10）

三层：
1. **11 阶段 wire 测试对齐**：Rust 引擎跑 durable protocol 的 11 测试（§4.1-4.11）= 对齐 Python。阶段 7/8 新建 skill/hermes wire 测试。
2. **Rust 单元测试**：sse.rs（golden snapshot）/ mixin（spring-back 时机）/ scorer（校验+降级+阈值）/ pool（槽位移交+I1）/ memory（拼接）。
3. **golden snapshot**：Python 版捕获真实 SSE（Claude + OAI responses + chat_completions 含 tool_use 分片）作 fixture，Rust parser 喂同输入断言 SseEvent 序列等价。

自定义 header/payload 用请求构造单测（断言组装出的 header map + payload 与 Python 字节级/语义等价）。

## 13. 双轨接管路线（11 阶段）

0 stdio 协议+握手+韧性(§4.1/4.11) | 1 agent_loop+llmcore(Native)+核心工具(§4.3 S1) | 2 interrupt(§4.5 S3) | 3 多会话并发(§4.4 S2) | 4 MCP(§4.7 S5) | 5 llm/session(§4.8 S6) | 6 approval(§4.6 S4) | 7 skill+L1-L4(新wire) | 8 hermes自进化(新wire) | **MVP** | 9 autonomous(§4.9) | 10 slash(§4.10) | 11 切默认+Python退役。

每阶段：Rust 版跑通对应 wire 测试 = 对齐 Python → 该能力可切 Rust 引擎（前端经 durable protocol 无感切换）。

## 14. 关键风险

1. llmcore Native SSE 手撸 + 16+ 伪装 header → golden snapshot + header 对照表逐项验证。
2. agent_loop 生成器→channel → event schema 已定（TurnStart+TextChunk），display buffer 移 engine.rs。
3. inline_eval SOP 改写 → 全量扫 memory/（已识别 2 处），改写为 EngineCap 工具。
4. distill worker 编排 → 快照拷贝一致性 + 有界 channel 背压。
5. MVP 工作量大（~5600 LOC Rust + hermes/memory + 新 wire）→ 双轨共存 Python 兜底，不阻塞现网。

## 15. Spec Patch（回写 delta spec）

- `rust-engine` capability 补验收场景：SSE 分片重组正确性 / MixinSession spring-back 时机 / distill worker 非阻塞 / inline_eval SOP 改写后工具等价 / 跨 NativeClaude+Oai 混用 / queued interrupt（I1）。
- `tool-dispatch` Modified 补：drop-in .py→MCP server 迁移验收 / EngineCap+ExternalTool 分层 dispatch 优先级。
