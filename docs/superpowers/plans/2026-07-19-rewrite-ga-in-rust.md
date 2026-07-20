---
change: rewrite-ga-in-rust
design-doc: docs/superpowers/specs/2026-07-19-rewrite-ga-in-rust-design.md
base-ref: 95b3117dbb376bd10667e299ff83b1851cab330c
---

# rewrite-ga-in-rust 实施计划（GA 引擎 Rust 重构）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 GA 引擎从 Python 迁到 Rust，产出单二进制 `ga-engine`（无 Python runtime），MVP（阶段 0-8）功能等价 Python GA 核心 + 自进化，经 durable protocol v1 双轨共存，Python 版兜底。

**Architecture:** 单 Rust crate 多模块（`ga-engine` binary），依赖自上而下无环：`protocol <- llmcore <- tools <- agent_loop <- engine <- pool <- main`；`memory <- skill <- hermes`；hermes 与 agent_loop 经有界 mpsc channel 单向（无回调->无循环）。agent_loop 用 `mpsc::Sender<AgentEvent>` 推 `TurnStart`/`TextChunk` 两事件替代 Python 生成器 yield；display buffer 聚合移至 `engine.rs`。llmcore 只 Native（砍传统 Session 类），SSE 解析手撸 reqwest 保 16+ 伪装 header。工具双 trait（EngineCap 持 `&Engine` / ExternalTool 含 MCP），dispatch EngineCap 优先->ExternalTool->未知，inline_eval 砍掉由 EngineCap 显式工具替代。hermes 用 distill worker + Scorer trait + 熔断。per-task 池 + tokio Semaphore + 槽位移交 + I1 queued-interrupt fix。

**Tech Stack:** Rust (edition 2021)、tokio (full)、reqwest (stream feature 手撸 HTTP/SSE)、rmcp (MCP client)、serde + serde_json + serde_yaml、async-trait、uuid、regex、chrono；`cargo test` 单元测试 + Python wire harness（11 阶段对齐基座，零修改仓内 Python 源码）。

## Global Constraints

- **不改 Python 版引擎源码**：`ga_stdio.py`/`agent_loop.py`/`llmcore.py`/`ga.py`/`ga_utils.py`/`mcp_client.py`/`skill_loader.py`/`plugins/skill_evolution.py`/`tools/skill_manage.py`/`agentmain.py` 全部冻结为行为对齐基准，双轨共存。测试辅助文件 `tests/_protocol_helpers.py` 亦不修改；Rust 侧自建独立 wire harness（`ga-engine/tests/wire/`）。
- **Rust 单 crate 多模块**：根 `ga-engine/`，binary `ga-engine`。`Cargo.toml` 依赖：`reqwest={version="0.12",features=["stream","json"]}`、`tokio={version="1",features=["full"]}`、`rmcp`、`serde={version="1",features=["derive"]}`、`serde_json`、`serde_yaml`、`async-trait`、`uuid={version="1",features=["v4"]}`、`regex`、`chrono`。无 Python/CPython runtime。
- **llmcore 只 Native**：只实现 `NativeClaudeSession`/`NativeOAISession`/`MixinSession`（砍传统 `ClaudeSession`/`LLMSession`）。SSE 解析（`_parse_claude_sse`/`_parse_openai_sse`）完整迁移。HTTP 手撸 reqwest（非 SDK），保 GA 自定义 beta header + 伪装 payload。
- **agent_loop channel**：`mpsc::Sender<AgentEvent>` 推 `TurnStart{turn}` + `TextChunk(String)` 两变体；display buffer 聚合（DisplayUpdate/Done）在 `engine.rs` 非 agent_loop 层。
- **工具双 trait**：`EngineCap`（持 `&Engine`，编译期注册）+ `ExternalTool`（无句柄，含 `McpTool` wrapper）。dispatch `EngineCap 优先 -> ExternalTool -> 未知工具 yield`。`inline_eval` 移除，由 `register_done_hook`/`enter_plan_mode`/`get_history`/`get_memory_state` 等价替代。
- **hermes**：distill worker 常驻 tokio task + 有界 mpsc channel + `Scorer` trait + `InProcessScorer`/`SubagentScorer` + 阈值闸门 `GA_SKILL_SCORER_THRESHOLD=60` + 熔断 `MAX_AUTO_PATCH_PER_SKILL=5`。
- **per-task 池**：`tokio::sync::Semaphore`（max 4，env `GA_STDIO_MAX_CONCURRENCY`）+ `HashMap<task_id, TaskCtx>` + `VecDeque<DistillJob>` queued + 槽位移交 + I1 queued-interrupt fix（ctx 未占槽位时移出队列+done{interrupted}，不 abort 不 release）。
- **L1-L4 内存**：`memory.rs` 原样复刻 L1(`global_mem_insight.txt`)/L2(`global_mem.txt`)/L3(`memory/*.md`)/L4(`L4_raw_sessions/`)，与 Python 版**双向兼容**（同一 `memory/` 目录）。`memory/` 里的 `.py` 工具脚本经 `code_run(subprocess)` 间接调用，引擎不内嵌 Python。
- **base-ref**：`95b3117dbb376bd10667e299ff83b1851cab330c`。所有 Python 行号引用以该 ref 为准。
- **双轨接管路线（11 阶段）**：每阶段 Rust 版跑通对应 wire 测试 = 对齐 Python -> 该能力可切 Rust 引擎。MVP = 阶段 0-8。

## File Structure（Crate 模块树，Design Doc §3）

```
ga-engine/
├─ Cargo.toml
├─ src/
│  ├─ main.rs                    # tokio runtime + BridgeCore 组装 + serve() stdin 循环
│  ├─ protocol/                   # durable protocol v1（最底层）
│  │  ├─ mod.rs
│  │  ├─ frame.rs                 # Frame{id,type,version} serde + parse_line/serialize
│  │  ├─ dispatch.rs              # 路由表 + required fields guard + not_initialized guard
│  │  └─ handlers.rs              # initialize/task_start/task_interrupt/approval/slash/llm/session/mcp_list
│  ├─ llmcore/
│  │  ├─ mod.rs
│  │  ├─ session.rs              # Session trait + BaseSession(cfg 字段全集) + history/trim/compress
│  │  ├─ claude_native.rs        # NativeClaudeSession（16+ header + beta + ?beta=true + context_management + metadata + fake_cc + cache_control）
│  │  ├─ oai_native.rs          # NativeOAISession（codex_exec 伪装 + responses/chat_completions + _stamp_oai_cache_markers）
│  │  ├─ mixin.rs                # MixinSession 故障转移 + spring-back
│  │  ├─ sse.rs                  # SseEvent enum + ClaudeSseParser + OaiResponsesParser + OaiChatCompletionsParser + _try_parse_tool_args
│  │  └─ retry.rs                # stream_with_retry（reqwest + 重试退避 + retry-after）
│  ├─ tools/
│  │  ├─ mod.rs                  # EngineCap + ExternalTool trait + dispatch 双表 + hook 两层
│  │  ├─ builtin.rs              # file_read/file_write/file_patch/code_run(subprocess)
│  │  ├─ engine_caps.rs          # register_done_hook/enter_plan_mode/get_history/get_memory_state/checkpoint/ask_user/skill_query/skill_manage
│  │  └─ mcp.rs                  # rmcp 客户端 + McpTool wrapper（impl ExternalTool）
│  ├─ agent_loop.rs              # tokio task + mpsc::Sender<AgentEvent>（TurnStart/TextChunk），纯控制流
│  ├─ engine.rs                  # GenericAgent 等价 + put_task + display buffer 聚合（DisplayUpdate/Done/SlashDone）
│  ├─ pool.rs                    # per-task 池 + tokio Semaphore + 槽位移交 + I1 fix
│  ├─ skill/
│  │  ├─ mod.rs
│  │  ├─ loader.rs               # discover/get_detail/sync_to_l1（serde_yaml frontmatter + mtime 缓存）
│  │  └─ manage.rs               # skill_manage（EngineCap 内置，CRUD + provenance gate + brief）
│  ├─ memory.rs                   # L1-L4 文件读写 + prompt 拼接 + L4 压缩
│  └─ hermes/
│     ├─ mod.rs
│     ├─ distill.rs              # DistillJob + DistillWorker（rx + scorer + llm + skill_store）
│     └─ scorer.rs               # Scorer trait + InProcessScorer/SubagentScorer + Verdict + 熔断
└─ tests/
   ├─ wire/                      # 11 阶段 wire 测试（spawn ga-engine 二进制，复用 Python 断言结构）
   │  ├─ harness.py              # BridgeProc 调 ga-engine（零修改 tests/_protocol_helpers.py）
   │  └─ test_protocol_*.py      # 对应 §4.1-4.11
   └─ fixtures/
      └─ sse/                    # golden snapshot（Claude/OAI responses/chat_completions 真实 SSE 录制）
```

**依赖方向**：`protocol <- llmcore <- tools <- agent_loop <- engine <- pool <- main`；`memory <- skill <- hermes`；hermes 与 agent_loop 经 channel 单向（agent_loop 推 DistillJob，hermes 消费，无回调）-> 无循环。

---

## Phase 0：脚手架 + stdio 协议层（阶段 0，§4.1/§4.11）

对齐 tasks.md §1（前置与脚手架）+ §2（stdio 协议层）。wire 测试 §4.1 transport + §4.11 resilience。

### Task 1: Rust crate 脚手架 + golden snapshot fixture 捕获

**Files:**
- Create: `ga-engine/Cargo.toml`
- Create: `ga-engine/src/main.rs`
- Create: `ga-engine/src/protocol/mod.rs`, `frame.rs`, `dispatch.rs`, `handlers.rs`
- Create: `ga-engine/tests/fixtures/sse/.gitkeep`
- Create: `ga-engine/tests/wire/harness.py`
- Test: `ga-engine/src/protocol/frame.rs`（`#[cfg(test)]` 内联）

**Interfaces:**
- Consumes: durable protocol v1 契约（id/type/version newline-delimited JSON on stdin/stdout）
- Produces: `Frame` struct、`parse_line`/`serialize` 函数、可 `cargo build` 的 crate 骨架、`harness.py` 的 `BridgeProc` 类（spawn `ga-engine` 二进制）

- [ ] **Step 1: 写 Cargo.toml + main.rs 骨架**

`ga-engine/Cargo.toml`：
```toml
[package]
name = "ga-engine"
version = "0.1.0"
edition = "2021"

[dependencies]
reqwest = { version = "0.12", features = ["stream", "json"] }
tokio = { version = "1", features = ["full"] }
rmcp = "0.1"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
serde_yaml = "0.9"
async-trait = "0.1"
uuid = { version = "1", features = ["v4"] }
regex = "1"
chrono = "0.4"

[[bin]]
name = "ga-engine"
path = "src/main.rs"
```

`ga-engine/src/main.rs`（骨架，Task 2/3 填充）：
```rust
use std::io::BufRead;

mod protocol;
mod llmcore;
mod tools;
mod agent_loop;
mod engine;
mod pool;
mod skill;
mod memory;
mod hermes;

#[tokio::main]
async fn main() {
    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let line = match line { Ok(l) => l, Err(_) => break };
        let stripped = line.trim();
        if stripped.is_empty() { continue; }
        eprintln!("[skeleton] recv: {}", &stripped[..stripped.len().min(80)]);
    }
}
```

- [ ] **Step 2: 写 frame.rs 失败测试**

`ga-engine/src/protocol/frame.rs` 末尾：
```rust
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn parse_line_valid_json_returns_frame() {
        let f = parse_line(r#"{"id":1,"type":"initialize","version":"1"}"#);
        assert!(f.is_some());
        let f = f.unwrap();
        assert_eq!(f.id, 1);
        assert_eq!(f.msg_type, "initialize");
    }
    #[test]
    fn parse_line_malformed_returns_none() {
        assert!(parse_line("this is not json").is_none());
        assert!(parse_line("").is_none());
        assert!(parse_line("[1,2]").is_none()); // not a dict
    }
    #[test]
    fn serialize_emits_compact_json_with_newline() {
        let f = Frame::new(1, "ready", "1");
        let s = serialize(&f);
        assert!(s.ends_with('\n'));
    }
}
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd /d/GenericAgent/ga-engine && cargo test --lib protocol::frame 2>&1 | tail -5`
Expected: FAIL（`parse_line`/`serialize`/`Frame` 未定义）

- [ ] **Step 4: 实现 frame.rs**

```rust
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Frame {
    pub id: Value,            // int 或 string
    #[serde(rename = "type")]
    pub msg_type: String,
    pub version: String,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, Value>,
}

impl Frame {
    pub fn new(id: impl Into<Value>, msg_type: &str, version: &str) -> Self {
        Self { id: id.into(), msg_type: msg_type.to_string(), version: version.to_string(),
               extra: serde_json::Map::new() }
    }
}

pub fn parse_line(line: &str) -> Option<Frame> {
    let line = line.trim();
    if line.is_empty() { return None; }
    let v: Value = serde_json::from_str(line).ok()?;
    if !v.is_object() { return None; }
    serde_json::from_value(v).ok()
}

pub fn serialize(f: &Frame) -> String {
    serde_json::to_string(f).unwrap() + "\n"
}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /d/GenericAgent/ga-engine && cargo test --lib protocol::frame 2>&1 | tail -5`
Expected: PASS (3 tests)

- [ ] **Step 6: 建 wire harness.py（spawn ga-engine，零修改仓内 Python 文件）**

`ga-engine/tests/wire/harness.py`：复刻 `tests/_protocol_helpers.py` 的 `BridgeProc`，但 spawn `[GA_ENGINE_BIN or "ga-engine"]` 而非 `python -m ga_stdio`。提供 `initialize(bp, caps)`。env `GA_ENGINE_BIN` 覆写二进制路径。

- [ ] **Step 7: 建 golden snapshot 捕获脚本（一次性，不改 Python 源码）**

`ga-engine/tests/fixtures/sse/capture.py`：import `llmcore`（仓内 Python，只读调用），对 Claude/OAI responses/chat_completions 跑一次真实流式调用，把 `resp.iter_lines()` 原始字节落盘成 `claude_tool_use.txt`/`oai_responses_fc.txt`/`oai_chat_tc.txt`。手动运行一次（需 `GA_HAS_LLM` 或 `MYKEY_PATH`），产出 fixture 后提交。无 LLM 环境时跳过此 step，Task 4 用手写极小 SSE 片段先过单测。

- [ ] **Step 8: Commit**

```bash
cd /d/GenericAgent
git add ga-engine/
git commit -m "feat(ga-engine): scaffold Rust crate + protocol frame + wire harness + sse fixture capture"
```

### Task 2: stdio 读写循环 + initialize/ready 握手 + capability 协商

**Files:**
- Modify: `ga-engine/src/main.rs`（接 stdin 循环 + 调 dispatch）
- Modify: `ga-engine/src/protocol/dispatch.rs`（路由表 + required fields + not_initialized guard）
- Modify: `ga-engine/src/protocol/handlers.rs`（handle_initialize）
- Test: `ga-engine/tests/wire/test_protocol_transport.py`（对齐 §4.1）

**Interfaces:**
- Consumes: `protocol::frame::{parse_line, serialize, Frame}`（Task 1）
- Produces: `BridgeCore` struct、`VERSION="1"`、`SERVER_CAPABILITIES`、`handle_initialize` -> `ready{capabilities, agent_info{name,mcp_connected,llm_count}}`

- [ ] **Step 1: 写 wire 失败测试**（对齐 `tests/test_protocol_transport.py::test_handshake_returns_ready_with_caps_and_agent_info`）

`ga-engine/tests/wire/test_protocol_transport.py`：import `harness`，发 initialize -> 收 ready -> 断言 `capabilities` 非空 + `agent_info.name=="GenericAgent"` + 含 `mcp_connected`/`llm_count`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /d/GenericAgent/ga-engine && cargo build && python -m pytest tests/wire/test_protocol_transport.py -v 2>&1 | tail -5`
Expected: FAIL（二进制未实现握手，超时无 ready）

- [ ] **Step 3: 实现 dispatch.rs + handlers.rs + BridgeCore**

`dispatch.rs`：`required_fields = ["id","type","version"]`；缺字段 -> `send_error(bad_request)`；`initialize` 不要求 initialized；其余 mtype 要求 `initialized==true` 否则 `not_initialized`。路由：`task/start`/`task/interrupt`/`approval/response`/`slash/cmd`/`llm/list`/`llm/select`/`session/resume`/`mcp/list`，未命中 -> `unknown_type`。

`handlers.rs` `handle_initialize`：校验 `caps` 全在 `SERVER_CAPABILITIES`（缺 -> `capability_unsupported`+`missing`）；置 `initialized=true`；`llm_count`/`mcp_connected` 探测（MVP 阶段 0 先返回 0，阶段 5/4 填实）-> 发 `ready{capabilities, agent_info{name:"GenericAgent", mcp_connected, llm_count}}`。

`main.rs`：stdin 逐行 -> `parse_line` -> None 则 `bad_json` -> `dispatch` -> 异常则 `internal_error`。stdout 写 JSON（`serialize`），写锁（`Mutex<StdoutLock>`）。**stdout 纯净性（C1 对齐）**：Rust 默认 print 不混入 stdout wire（不像 Python 需 dup fd）；所有诊断走 `eprintln!`。

- [ ] **Step 4: 跑 wire 测试确认通过**

Run: `cd /d/GenericAgent/ga-engine && cargo build && python -m pytest tests/wire/test_protocol_transport.py -v 2>&1 | tail -5`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /d/GenericAgent
git add ga-engine/
git commit -m "feat(ga-engine): stdio loop + initialize/ready handshake + capability negotiation"
```

### Task 3: 错误韧性 + stdout 纯净性 + 对齐 §4.1/§4.11

**Files:**
- Modify: `ga-engine/src/main.rs`（bad_json 不崩继续 + EOF 退出 + internal_error 包裹）
- Test: `ga-engine/tests/wire/test_protocol_transport.py`（补 malformed_json + first_line_is_json）、`test_protocol_resilience.py`（§4.11）

**Interfaces:**
- Consumes: Task 2 的 dispatch
- Produces: `send_error(code,message,original_id,**extra)`；EOF 自然退出（parent 见 stdout EOF）

- [ ] **Step 1: 写 wire 失败测试**

补 `test_malformed_json_emits_bad_json_error_and_keeps_running`（对齐 `tests/test_protocol_transport.py`：发畸形行 -> 收 `error{code:bad_json}` -> 再发 `mcp/list` 仍响应）+ `test_stdout_first_line_is_json_no_engine_diagnostic`（raw_recv 首行必须 JSON 解析通过）+ `test_protocol_resilience.py` 两测（`not_initialized` 拒业务消息 + kill child -> recv None EOF）。

- [ ] **Step 2: 跑确认失败** -> Step 3: 实现（bad_json 路径不 break；`eprintln!` 全走 stderr；EOF break 循环；not_initialized 在 dispatch） -> Step 4: 跑通过 -> Step 5: Commit `feat(ga-engine): error resilience + stdout wire purity + align wire 4.1/4.11`

---

## Phase 1：agent_loop + llmcore + 核心工具（阶段 1，§4.3 S1）

对齐 tasks.md §3（3.1-3.8）。wire 测试 §4.3 single_task。**含 3 个关键风险任务：SSE 手撸（Task 4-5）、MixinSession 状态机（Task 8）、agent_loop channel 重构（Task 9）。**

### Task 4: ClaudeSseParser（golden snapshot 对齐）★关键风险

对齐 Python `llmcore.py:255-361` `_parse_claude_sse`。7 事件 + 4 content_block_delta 子类型 + warn 插入。

**Files:**
- Create: `ga-engine/src/llmcore/sse.rs`
- Create: `ga-engine/tests/fixtures/sse/claude_tool_use.txt`（golden snapshot）
- Test: `ga-engine/src/llmcore/sse.rs`（`#[cfg(test)]`）

**Interfaces:**
- Consumes: SSE 行流（`impl Iterator<Item=&str>` 行）
- Produces: `SseEvent` enum（`Text`/`Thinking`/`ToolUse`/`Usage`/`Warn`/`Done`）、`ClaudeSseParser` struct（喂行 -> yield `SseEvent`，收尾返回 `Vec<ContentBlock>`）

- [ ] **Step 1: 写 SseEvent enum + ClaudeSseParser 失败测试**（golden snapshot）

```rust
#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> String { std::fs::read_to_string("../tests/fixtures/sse/claude_tool_use.txt").unwrap_or_default() }
    #[test]
    fn claude_parser_yields_text_then_tool_use() {
        let mut parser = ClaudeSseParser::new();
        let mut events = vec![];
        for line in fixture().lines() {
            if let Some(ev) = parser.feed(line) { events.push(ev); }
        }
        let blocks = parser.finish();
        assert!(events.iter().any(|e| matches!(e, SseEvent::Text(_))));
        assert!(blocks.iter().any(|b| b.kind == BlockKind::ToolUse));
        let tu = blocks.iter().find(|b| b.kind == BlockKind::ToolUse).unwrap();
        assert!(tu.input.is_object());
    }
    #[test]
    fn claude_parser_missing_message_stop_inserts_warn() {
        let mut p = ClaudeSseParser::new();
        for line in fixture().lines() { p.feed(line); }
        let blocks = p.finish();
        assert!(blocks.iter().any(|b| b.kind == BlockKind::Text && b.text.contains("流异常中断")));
    }
}
```

若无 LLM 录制 fixture，先用极小手写 SSE：`data: {"type":"message_start","message":{"usage":{}}}` / `data: {"type":"content_block_start","content_block":{"type":"text","text":""}}` / `data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}` / `data: {"type":"content_block_stop"}` / `data: {"type":"message_stop"}`。

- [ ] **Step 2: 跑确认失败** -> Step 3: 实现 `ClaudeSseParser`（7 事件分发：`message_start` 记 usage；`content_block_start` 建 current_block（text/thinking/tool_use+tool_json_buf 清空）；`content_block_delta` 4 子类型：text_delta 累积+yield Text、thinking_delta 累积、signature_delta 累积、input_json_delta 拼 partial_json 到 buf；`content_block_stop` 时 `serde_json::from_str(buf)` 重组 ToolUse input（失败 -> `{"_raw":buf}`）；`message_delta` 记 stop_reason；`message_stop` 置 flag；`error` -> warn break。收尾：未收 message_stop 且无 stop_reason -> warn "流异常中断"；stop_reason==max_tokens -> warn "truncated"。warn 插入位置：第一个 tool_use block 前。逐项对齐 `llmcore.py:255-361`。） -> Step 4: 跑通过 -> Step 5: Commit `feat(llmcore): ClaudeSseParser with golden snapshot (align llmcore.py:255-361)`

### Task 5: OaiSseParser（responses + chat_completions）+ _try_parse_tool_args

对齐 Python `llmcore.py:385-530` `_parse_openai_sse`（两 api_mode 分支）+ `:364-382` `_try_parse_tool_args`。

**Files:**
- Modify: `ga-engine/src/llmcore/sse.rs`（加 `OaiResponsesParser` + `OaiChatCompletionsParser`）
- Test: 同上 `#[cfg(test)]`

**Interfaces:**
- Consumes: SSE 行流 + `api_mode: "responses"|"chat_completions"`
- Produces: 两 parser struct，复用 `ContentBlock`/`SseEvent`

- [ ] **Step 1: 写失败测试**（responses 分片按 output_index 聚合 + chat_completions delta.tool_calls[] 按 index 聚合 + reasoning_content + `{..}{..}` 连写切分） -> Step 2: 跑失败 -> Step 3: 实现（responses：`response.output_text.delta` yield + `response.output_item.added` 建 fc_buf[idx] + `response.function_call_arguments.delta` 按 idx 聚合 + `response.completed` 记 usage；chat_completions：`delta.tool_calls[]` 按 index 聚合 + `delta.reasoning_content`/`reasoning` 累积 thinking + `delta.content` yield + usage；`_try_parse_tool_args`：先 `serde_json::from_str`，失败用 `regex::split(r"(?<=\})(?=\{)")` 切多 dict。逐项对齐 `llmcore.py:385-530`。） -> Step 4: 跑通过 -> Step 5: Commit `feat(llmcore): OaiSseParser responses + chat_completions + _try_parse_tool_args`

### Task 6: NativeClaudeSession header/payload + stream_with_retry ★关键风险

对齐 Python `llmcore.py:1236-1353` `NativeClaudeSession.raw_ask` + `:636-731` `_stream_with_retry`。reqwest 手撸，保 16+ 伪装 header。

**Files:**
- Create: `ga-engine/src/llmcore/session.rs`（`Session` trait + `BaseSession` cfg 字段全集）
- Create: `ga-engine/src/llmcore/claude_native.rs`
- Create: `ga-engine/src/llmcore/retry.rs`
- Test: `claude_native.rs` + `retry.rs`（`#[cfg(test)]`）

**Interfaces:**
- Consumes: `mykey` cfg（api_key/api_base/model/temperature/max_tokens/stream/read_timeout/...）、`ClaudeSseParser`（Task 4）
- Produces: `NativeClaudeSession` impl `Session`、`Session::chat(messages, tools) -> Vec<ContentBlock>`（async）、`stream_with_retry(sess, url, headers, payload, parse_fn) -> Vec<ContentBlock>`

- [ ] **Step 1: 写 header 构造失败测试**（断言组装出的 header map + payload 与 Python `llmcore.py:1268-1340` 语义等价）

```rust
#[test]
fn claude_headers_contain_16_disguise_plus_beta() {
    let sess = NativeClaudeSession::new(test_cfg());
    let (headers, _payload) = sess.build_request(&vec![]);
    assert_eq!(headers.get("user-agent").unwrap(), "claude-cli/2.1.152 (native, cli)");
    assert_eq!(headers.get("x-app").unwrap(), "cli");
    assert!(headers.get("anthropic-beta").unwrap().to_str().unwrap()
            .contains("claude-code-20250219"));
    assert!(headers.get("anthropic-beta").unwrap().to_str().unwrap()
            .contains("context-management-2025-06-27"));
    assert!(headers.get("X-Stainless-Lang").is_some());
    assert!(headers.get("X-Claude-Code-Session-Id").is_some());
}
#[test]
fn sk_ant_key_uses_x_api_key_else_authorization_bearer() {
    let mut cfg = test_cfg(); cfg.api_key = "sk-ant-test123".into();
    let (h,_) = NativeClaudeSession::new(cfg).build_request(&vec![]);
    assert_eq!(h.get("x-api-key").unwrap(), "sk-ant-test123");
    assert!(h.get("authorization").is_none());
    let mut cfg = test_cfg(); cfg.api_key = "sk-test".into();
    let (h,_) = NativeClaudeSession::new(cfg).build_request(&vec![]);
    assert!(h.get("authorization").unwrap().to_str().unwrap().contains("Bearer sk-test"));
}
#[test]
fn beta_1m_inserted_at_index1_and_model_suffix_stripped() {
    let mut cfg = test_cfg(); cfg.model = "claude-sonnet[1m]".into();
    let (h, payload) = NativeClaudeSession::new(cfg).build_request(&vec![]);
    let beta = h.get("anthropic-beta").unwrap().to_str().unwrap();
    let parts: Vec<&str> = beta.split(',').collect();
    assert_eq!(parts[1], "context-1m-2025-08-07");
    assert!(serde_json::to_string(&payload["model"]).unwrap().contains("claude-sonnet"));
    assert!(!serde_json::to_string(&payload["model"]).unwrap().contains("[1m]"));
}
#[test]
fn payload_has_context_management_fake_cc_metadata_cache_control() {
    let sess = NativeClaudeSession::new(test_cfg());
    let (_, payload) = sess.build_request(&vec![]);
    assert_eq!(payload["context_management"]["edits"][0]["type"], "clear_thinking_20251015");
    assert!(payload["metadata"]["user_id"].is_string());
    assert_eq!(payload["system"][0]["text"], "You are Claude Code, Anthropic's official CLI for Claude.");
    assert_eq!(payload["system"][0]["cache_control"]["type"], "ephemeral");
}
```

- [ ] **Step 2: 跑失败** -> Step 3: 实现（`Session` trait：`async fn chat(&self, messages, tools) -> Vec<ContentBlock>`；`BaseSession` 持 cfg/api_key/model/temperature/max_tokens/stream/read_timeout/history/lock。`NativeClaudeSession::build_request`：beta_parts 6 项 + `[1m]` 条件 insert(1) + 去后缀；16+ header；sk-ant- 判定；payload max_tokens/stream/temperature/thinking/context_management{clear_thinking_20251015,keep:all}/fake_cc 时 thinking{adaptive}+output_config{effort:medium}/metadata.user_id 紧凑 JSON/separators=(",",":")/tools->openai_tools_to_claude + 末项 cache_control/system list 假 cc prompt + cache_control/最后 2 user block content 末项 cache_control；url + `?beta=true`。`stream_with_retry`：reqwest POST stream，status>=400 且在 `_RETRYABLE`{408,409,425,429,500,502,503,504,520-527} 且 attempt<max_retries -> retry-after 或 `min(30,1.5*2^attempt)` 退避；正常 -> `ClaudeSseParser` 逐 chunk；Timeout/ConnectionError/ChunkedEncoding 重试；异常收尾 yield `!!!Error:` 文本块。逐项对齐 `llmcore.py:636-731,1236-1353`。） -> Step 4: 跑通过 -> Step 5: Commit `feat(llmcore): NativeClaudeSession headers/payload + stream_with_retry (align llmcore.py:1236-1353,636-731)`

### Task 7: NativeOAISession + _stamp_oai_cache_markers

对齐 Python `llmcore.py:1399-1407` `NativeOAISession` + `:615-633` `_stamp_oai_cache_markers` + `:733-810` `_openai_stream`/`_prepare_oai_tools`/`_to_responses_input`/`_msgs_claude2oai`。

**Files:**
- Create: `ga-engine/src/llmcore/oai_native.rs`
- Test: `oai_native.rs`（`#[cfg(test)]`）

**Interfaces:**
- Consumes: `OaiResponsesParser`/`OaiChatCompletionsParser`（Task 5）+ `stream_with_retry`（Task 6）
- Produces: `NativeOAISession`（继承 NativeClaudeSession 基础，覆写 `raw_ask`）

- [ ] **Step 1: 写失败测试**（codex_exec UA + originator header + responses 模式 prompt_cache_key/client_metadata/include + chat_completions stream_options.include_usage + gpt-5/o1-o4 前缀 max_completion_tokens + claude-via-OAI cache markers） -> Step 2: 跑失败 -> Step 3: 实现（`native_ua = "codex_exec/0.139.0 (Windows 10.0.26200; x86_64) unknown (codex_exec; 0.139.0)"`；`originator: codex_exec` header；responses 模式 payload 含 `prompt_cache_key`/`client_metadata{x-codex-window-id,x-codex-installation-id}`/`include:["reasoning.encrypted_content"]`；chat_completions `stream_options.include_usage` + model gpt-5/o1/o2/o3/o4 前缀 -> `max_completion_tokens` 否则 `max_tokens`；`_stamp_oai_cache_markers`：model 含 claude/anthropic 时给最后 2 user 消息加 `cache_control:ephemeral`。逐项对齐 `llmcore.py:615-633,733-810,1399-1407`。） -> Step 4: 跑通过 -> Step 5: Commit `feat(llmcore): NativeOAISession codex_exec disguise + _stamp_oai_cache_markers (align llmcore.py:1399-1407,615-633)`

### Task 8: MixinSession 故障转移 + spring-back 状态机 ★关键风险

对齐 Python `llmcore.py:1697-1826` `MixinSession`。

**Files:**
- Create: `ga-engine/src/llmcore/mixin.rs`
- Test: `mixin.rs`（`#[cfg(test)]`，含 spring-back 时机 + 跨协议混用）

**Interfaces:**
- Consumes: `Vec<Box<dyn Session>>`（各 Native session）
- Produces: `MixinSession` impl `Session`（广播 system/tools/temperature/max_tokens/reasoning_effort/history/stream/read_timeout；tools 各 session 内部转 schema）

- [ ] **Step 1: 写失败测试**（★关键风险）

```rust
#[test]
fn spring_back_to_primary_after_spring_sec() {
    let mut m = test_mixin_two_native();
    m.set_cur_idx(1); m.set_switched_at(Instant::now() - Duration::from_secs(301));
    assert_eq!(m.pick(), 0); // spring_sec=300 超时 -> 切回 primary
}
#[test]
fn raw_ask_round_robins_on_error_chunk() {
    let m = test_mixin_two_native_first_errors();
    let blocks = m.chat(vec![], vec![]);
    assert_eq!(m.cur_idx(), 1); // s0 yield !!!Error -> 切 s1 -> 成功 lock cur_idx=1
}
#[test]
fn partial_failure_stream_anomaly_advances_idx() {
    let m = test_mixin_partial_failure();
    let _ = m.chat(vec![], vec![]);
    assert_eq!(m.cur_idx(), 1); // 成功 attempt==0 但 last_chunk 含 "[!!! 流异常中断" -> 切 idx+1
}
#[test]
fn cross_protocol_claude_plus_oai_tools_broadcast() {
    let m = test_mixin_claude_plus_oai();
    m.set_tools(test_openai_tools_schema());
    assert!(m.session_at(0).tools_schema_is_claude());   // input_schema
    assert!(m.session_at(1).tools_schema_is_functions()); // functions
}
```

- [ ] **Step 2: 跑失败** -> Step 3: 实现（`MixinSession` 持 `Vec<Box<dyn Session>>` + `cur_idx`/`switched_at`/`spring_sec`(300)/`retries`(3)/`base_delay`(1.5)。`_BROADCAST_ATTRS` 8 项 setter 广播；`tools` 给 `NativeClaudeSession` 时 `openai_tools_to_claude` 转换。`pick()`：cur_idx!=0 且 elapsed>spring_sec -> cur_idx=0。`raw_ask`：base=pick, n=len, 按 `(base+attempt)%n` 轮询；error chunk（`!!!Error:`/`[Error:` 开头）未 yield 时吞；成功 attempt>0 -> lock cur_idx=idx+switched_at=now；成功 attempt==0 但 last_chunk 含 `[!!! 流异常中断` 且 n>1 -> cur_idx=(idx+1)%n；失败 -> 下一个 attempt；整轮失败 `time::sleep(min(30, base_delay*1.5^round))`。同 Native 组断言。逐项对齐 `llmcore.py:1697-1826`。） -> Step 4: 跑通过 -> Step 5: Commit `feat(llmcore): MixinSession failover + spring-back state machine (align llmcore.py:1697-1826)`

---

### Task 9: agent_loop channel 重构 + engine.rs display buffer ★关键风险

对齐 Python `agent_loop.py:81-147` `agent_runner_loop` + `agentmain.py:218-235` drain。生成器->mpsc channel。

**Files:**
- Create: `ga-engine/src/agent_loop.rs`
- Create: `ga-engine/src/engine.rs`
- Test: `agent_loop.rs` + `engine.rs`（`#[cfg(test)]`）

**Interfaces:**
- Consumes: `Session`（Task 6-8）+ `tools::dispatch`（Task 11）
- Produces: `AgentEvent` enum（`TurnStart{turn}`/`TextChunk(String)`）、`agent_loop(client, sys_prompt, user_input, handler, tools_schema, tx: mpsc::Sender<AgentEvent>)` tokio task、`Engine` struct（`put_task(prompt) -> mpsc::Receiver<DisplayUpdate>`，display buffer 聚合）

- [ ] **Step 1: 写失败测试**

```rust
#[tokio::test]
async fn agent_loop_pushes_turn_start_then_text_chunks() {
    let (tx, mut rx) = mpsc::channel::<AgentEvent>(64);
    let handler = test_handler();
    let client = test_session_yields_text_then_done();
    tokio::spawn(agent_loop(client, "sys".into(), "hi".into(), handler, vec![], tx));
    let first = rx.recv().await.unwrap();
    assert!(matches!(first, AgentEvent::TurnStart { turn: 1 }));
    let mut chunks = vec![];
    while let Some(ev) = rx.recv().await {
        match ev {
            AgentEvent::TextChunk(s) => chunks.push(s),
            AgentEvent::TurnStart{..} => break,
        }
    }
    assert!(!chunks.is_empty());
}
#[test]
fn engine_aggregates_chunks_into_display_update() {
    // engine.rs 收 TurnStart+TextChunk -> 聚合成 DisplayUpdate{text,source,turn,recent_outputs}
    // 对齐 agentmain.py:221-235 的 full_resp 聚合逻辑（30 字节阈值 flush）
}
```

- [ ] **Step 2: 跑失败** -> Step 3: 实现（`AgentEvent` 两变体。`agent_loop`：tokio task，messages 初始化 system+user；循环 turn<max_turns：`tx.send(TurnStart{turn})` -> `client.chat()` 流式 -> 每个 text chunk `tx.send(TextChunk(s))` -> 解析 tool_calls -> `handler.dispatch` 每个工具 -> 工具输出 `tx.send(TextChunk)` -> turn_end_callback -> 组下轮 messages。hook（tool_before/tool_after/turn_after/agent_after）经 `tools::dispatch` 两层包裹。对齐 `agent_loop.py:81-147`。`Engine`：`put_task` -> mpsc channel；drain worker 收 `AgentEvent` 聚合成 `DisplayUpdate{text,source,turn,recent_outputs}` + 终态 `Done{text,source,turn,outputs}`（对齐 `agentmain.py:221-235` 的 full_resp 累积 + 30 字节阈值 flush + done）。） -> Step 4: 跑通过 -> Step 5: Commit `feat(engine): agent_loop mpsc channel + display buffer aggregation (align agent_loop.py:81-147, agentmain.py:218-235)`

### Task 10: 核心外部工具（file_read/file_write/file_patch/code_run subprocess）

对齐 Python `ga.py:181-199` `do_file_read` + `:148-179` `do_file_write` + `:134-146` `do_file_patch` + `:58-83` `do_code_run`（**砍 inline_eval 分支**）+ `ga_utils.py:66/218/240` `code_run`/`file_patch`/`file_read`。

**Files:**
- Create: `ga-engine/src/tools/builtin.rs`
- Test: `builtin.rs`（`#[cfg(test)]`，用 tempfile）

**Interfaces:**
- Consumes: `serde_json::Value`（工具 args）
- Produces: `FileRead`/`FileWrite`/`FilePatch`/`CodeRun` struct impl `ExternalTool` trait

- [ ] **Step 1: 写失败测试**（file_read 行号+keyword+截断；file_write overwrite/append/prepend + `<file_content>` 提取；file_patch old/new 替换；code_run subprocess python/bash + timeout + 截断；**code_run 不含 inline_eval 分支**） -> Step 2: 跑失败 -> Step 3: 实现（`code_run`：`tokio::process::Command::new("python"/"bash")` + `.arg(code)` + timeout + maxlen 截断 + stop_signal 轮询。**砍 inline_eval**：原 `do_code_run` 的 `args.get("inline_eval")` 分支整段删除，对应 `rust-engine` spec 的 inline_eval 移除要求。其余对齐 `ga_utils.py:66-217`。） -> Step 4: 跑通过 -> Step 5: Commit `feat(tools): builtin file_read/write/patch + code_run subprocess (inline_eval dropped)`

### Task 11: EngineCap + 双 trait dispatch + 编译期注册 + 对齐 §4.3

对齐 Python `agent_loop.py:47-68` `BaseHandler.dispatch`（method-track 优先->registry-track->未知）+ `ga.py` do_* method-track。Design Doc §7 工具分层。

**Files:**
- Create: `ga-engine/src/tools/mod.rs`（`EngineCap` + `ExternalTool` trait + `Dispatch` 双表 + hook 两层）
- Create: `ga-engine/src/tools/engine_caps.rs`（register_done_hook/enter_plan_mode/get_history/get_memory_state/checkpoint/ask_user/skill_query）
- Test: `tools/mod.rs`（`#[cfg(test)]`）+ wire `test_protocol_single_task.py`

**Interfaces:**
- Consumes: `Engine`（Task 9）+ `builtin`（Task 10）
- Produces: `EngineCap`/`ExternalTool` trait、`Dispatch` struct（EngineCap 表 + ExternalTool 表 + `call(name, args) -> ToolResult`）、`register_done_hook`/`enter_plan_mode`/`get_history`/`get_memory_state`/`ask_user`/`skill_query` EngineCap 实现

- [ ] **Step 1: 写失败测试**（EngineCap 优先于 ExternalTool 同名碰撞；ExternalTool fallback；未知工具 yield 文本；hook 两层包裹） -> Step 2: 跑失败 -> Step 3: 实现（`#[async_trait] trait EngineCap: Send+Sync { fn name(&self)->&str; async fn call(&self, engine:&Engine, args:Value)->ToolResult; }`；`#[async_trait] trait ExternalTool: Send+Sync { fn name(&self)->&str; fn schema(&self)->ToolSchema; async fn call(&self, args:Value)->ToolResult; }`。`Dispatch::call`：先查 EngineCap 表 -> 命中调 `cap.call(engine,args)` + tool_before/tool_after hook；未命中查 ExternalTool 表 -> 命中调 `ext.call(args)`；仍未命中 -> yield `TextChunk("未知工具: name")`。`register_done_hook`/`enter_plan_mode`/`get_history`/`get_memory_state` 持 `&Engine` 调内部，**替代 inline_eval**。对齐 `agent_loop.py:47-68` + Design Doc §7。） -> Step 4: 跑 wire `test_protocol_single_task.py`（对齐 §4.3 S1：task/start -> task/ack{running} -> task/delta+ -> task/done{completed}）确认通过 -> Step 5: Commit `feat(tools): EngineCap+ExternalTool dual dispatch + engine_caps + align wire 4.3 S1`

---

## Phase 2：task/interrupt（阶段 2，§4.5 S3）

对齐 tasks.md §4（4.1-4.2）。wire 测试 §4.5。**含 I1 queued-interrupt fix。**

### Task 12: task/interrupt + queued interrupt I1 fix + 对齐 §4.5

对齐 Python `ga_stdio.py:452-519` `handle_task_interrupt`（含 I1 queued 分支）。

**Files:**
- Modify: `ga-engine/src/protocol/handlers.rs`（`handle_task_start` + `handle_task_interrupt` + `_release_task`）
- Create: `ga-engine/src/pool.rs`（per-task 池骨架，Phase 3 填并发）
- Test: wire `test_protocol_interrupt.py`

**Interfaces:**
- Consumes: Task 9 `Engine`、Task 11 dispatch
- Produces: `TaskCtx` struct、`pool: HashMap<task_id, TaskCtx>`、`handle_task_interrupt`（queued 分支 + running 分支）

- [ ] **Step 1: 写 wire 失败测试**（对齐 `tests/test_protocol_interrupt.py`：task/start -> delta -> task/interrupt -> task/done{reason:interrupted}）+ 单测 queued interrupt（ctx.engine is None -> 移出 _queued+pool + done{interrupted}，不 abort 不 release） -> Step 2: 跑失败 -> Step 3: 实现（`handle_task_start`：分配 task_id + 注册 pool + 单任务串行 spawn（Phase 3 才上 semaphore）。`handle_task_interrupt`：ctx=None -> `unknown_task`；**queued 分支（ctx.engine is None，I1 fix）**：移出 `_queued`+pool + 发 `task/done{reason:interrupted}`，**不 abort 不 semaphore.release**；running 分支：`engine.abort()` -> 设 ctx.interrupted -> 发 `task/ack{status:interrupting}`；abort 异常 -> `interrupt_failed`。drain worker 收 done -> 据 ctx.interrupted 选 reason。逐项对齐 `ga_stdio.py:452-519`。） -> Step 4: 跑 wire 通过 -> Step 5: Commit `feat(pool): task/interrupt + queued I1 fix + align wire 4.5 S3`

---

## Phase 3：多会话并发（阶段 3，§4.4 S2）

对齐 tasks.md §5（5.1-5.2）。wire 测试 §4.4。

### Task 13: per-task 池 + tokio Semaphore + FIFO 排队 + 槽位移交 + 对齐 §4.4

对齐 Python `ga_stdio.py:390-398`（semaphore acquire(blocking=False)）+ `:535-562` `_release_task`（槽位移交非 release-then-wake）。

**Files:**
- Modify: `ga-engine/src/pool.rs`（`Semaphore` + `HashMap<task_id,TaskCtx>` + `VecDeque` queued）
- Modify: `ga-engine/src/protocol/handlers.rs`（`handle_task_start` 接 semaphore）
- Test: wire `test_protocol_multi_session.py`

**Interfaces:**
- Consumes: Task 12 `TaskCtx`
- Produces: `Pool` struct（`Semaphore::new(max_concurrency)` + `try_acquire` -> running/queued + `_release_task` 槽位移交）

- [ ] **Step 1: 写 wire 失败测试**（对齐 `tests/test_protocol_multi_session.py`：两并发 task/start -> 两 task_id 不交叉 -> 两 task/done 无泄漏）+ 单测槽位移交（释放时 pop queued -> spawn -> task/ack{running}） -> Step 2: 跑失败 -> Step 3: 实现（`max_concurrency` 默认 `env GA_STDIO_MAX_CONCURRENCY`(4)。`handle_task_start`：ctx 注册 pool -> `semaphore.try_acquire()` 成功 -> `task/ack{running}` + spawn；失败 -> `_queued.push_back` + `task/ack{queued}`。`_release_task`：pop pool + shutdown engine -> `_queued` 非空 -> pop front -> `_start_task_thread` + `task/ack{running}`（**槽位移交**，不 release semaphore）；空 -> `semaphore.add_permits(1)`。对齐 `ga_stdio.py:390-398,535-562`。） -> Step 4: 跑 wire 通过 -> Step 5: Commit `feat(pool): bounded concurrency + FIFO queue + slot hand-off + align wire 4.4 S2`

---

## Phase 4：MCP 可见性 + 调用（阶段 4，§4.7 S5）

对齐 tasks.md §6（6.1-6.4）。wire 测试 §4.7。

### Task 14: rmcp 客户端 + McpTool wrapper + drop-in 发现

对齐 Python `mcp_client.py` `MCPClientManager`/`MCPToolRegistry` + `ga.py:241-256` `do_mcp_call` + `tool-dispatch` spec MODIFIED（drop-in .py -> MCP server）。

**Files:**
- Create: `ga-engine/src/tools/mcp.rs`（rmcp client + `McpTool` impl `ExternalTool`）
- Modify: `ga-engine/src/tools/mod.rs`（注册 `McpTool` 到 ExternalTool 表）
- Test: `mcp.rs`（`#[cfg(test)]`，mock rmcp server）

**Interfaces:**
- Consumes: `rmcp` crate + tools/ 目录扫描
- Produces: `McpClient`（连 MCP server + `tools/list` + `tools/call`）、`McpTool` wrapper（impl `ExternalTool`，schema 从 `tools/list` 动态拿，call 走 `tools/call`）

- [ ] **Step 1: 写失败测试**（`tools/list` 返回工具 -> 注册到 ExternalTool 表 -> `tools/call` 调用返回结果；drop-in 扫描 tools/ 目录发现 stdio MCP server） -> Step 2: 跑失败 -> Step 3: 实现（`McpClient::new()`：rmcp transport 连 stdio MCP server；`tools/list()` -> `Vec<ToolSchema>`；`McpTool`：`name`/`schema()` 从 list 拿，`call(args)` 走 `tools/call`。drop-in 发现：扫 `tools/` 目录的 stdio MCP server（manifest 标识）-> 各起 `McpClient` -> 工具注册 ExternalTool 表。对齐 `mcp_client.py` + `tool-dispatch` spec MODIFIED。） -> Step 4: 跑通过 -> Step 5: Commit `feat(tools): rmcp client + McpTool wrapper + drop-in discovery`

### Task 15: mcp/list 协议消息 + 对齐 §4.7

对齐 Python `ga_stdio.py:1029-1078` `handle_mcp_list`。

**Files:**
- Modify: `ga-engine/src/protocol/handlers.rs`（`handle_mcp_list`）
- Test: wire `test_protocol_mcp_visibility.py`

- [ ] **Step 1: 写 wire 失败测试**（对齐 §4.7：mcp/list -> `servers:[{name, tools:[{name,description}]}]`，无 MCP 配置时 `servers:[]`） -> Step 2: 跑失败 -> Step 3: 实现（读 `McpClient` registry：每 server name -> `[{name, description}]`（inputSchema 省略——只报可见性）。snapshot 在锁内（对齐 `ga_stdio.py:1029-1078` 的 locked snapshot）。无 server -> `servers:[]`。） -> Step 4: 跑 wire 通过 -> Step 5: Commit `feat(protocol): mcp/list + align wire 4.7 S5`

---

## Phase 5：LLM 切换 + 会话恢复（阶段 5，§4.8 S6）

对齐 tasks.md §7（7.1-7.3）。wire 测试 §4.8。

### Task 16: llm/list + llm/select + session/resume + 对齐 §4.8

对齐 Python `ga_stdio.py:981-1027`（`_ctx_for` guard + `handle_llm_list`/`handle_llm_select`/`handle_session_resume`）+ `agentmain.py:117-135` `next_llm`/`list_llms`。

**Files:**
- Modify: `ga-engine/src/protocol/handlers.rs`（三个 handler + `_ctx_for`）
- Modify: `ga-engine/src/engine.rs`（`list_llms`/`next_llm`/`set_history`）
- Test: wire `test_protocol_llm_session.py`

- [ ] **Step 1: 写 wire 失败测试**（对齐 §4.8：task/start -> delta -> llm/list 返回 llms+current_no -> llm/select -> llm/ack -> session/resume{history,llm_no} -> session/ack；unknown_task guard） -> Step 2: 跑失败 -> Step 3: 实现（`_ctx_for`：ctx None 或 engine None -> `unknown_task`。`handle_llm_list`：`engine.list_llms()` -> `[{no,name,current}]` + `current_no`。`handle_llm_select`：`engine.next_llm(n)` -> `llm/ack{selected_no, ok:true}`。`handle_session_resume`：**先** `engine.set_backend_history(history)`，**后** `next_llm(llm_no)`（对齐 `ga_stdio.py:979` 注释：next_llm 内部 copy history，先 set 后 switch 让 history 存活）-> `session/ack{ok:true}`。） -> Step 4: 跑 wire 通过 -> Step 5: Commit `feat(protocol): llm/list + llm/select + session/resume + align wire 4.8 S6`

---

## Phase 6：approval 人机回路（阶段 6，§4.6 S4）

对齐 tasks.md §8（8.1-8.2）。wire 测试 §4.6。

### Task 17: approval/request + ask_user EngineCap + 对齐 §4.6

对齐 Python `ga_stdio.py:783-867`（`_patch_ask_user` + `_on_approval_request` + `handle_approval_response`）+ `ga.py:85-90` `do_ask_user`。Rust 用 `tokio::sync::oneshot` 替代 Python `threading.Event`。

**Files:**
- Modify: `ga-engine/src/tools/engine_caps.rs`（`AskUser` EngineCap，阻塞 on `oneshot::Receiver`）
- Modify: `ga-engine/src/protocol/handlers.rs`（`handle_approval_response` + `_pending_approvals: HashMap<(task_id,tool_id), oneshot::Sender<ApprovalDecision>>`）
- Test: wire `test_protocol_approval.py`

- [ ] **Step 1: 写 wire 失败测试**（对齐 §4.6：task/start 提示调 ask_user -> approval/request -> approval/response{approve,input} -> task/done{completed}；reject 路径） -> Step 2: 跑失败 -> Step 3: 实现（`AskUser` EngineCap：`call()` -> 分配 tool_id + 建 `oneshot::channel` + 存 `_pending_approvals[(task_id,tool_id)]=tx` -> 发 `approval/request{task_id,tool_id,prompt,options}` -> `rx.await`（阻塞 agent_loop task，不阻塞 tokio runtime）-> 返回 input 或 REJECTED。`handle_approval_response`：pop slot -> `tx.send(decision+input)` -> `approval/ack{status:accepted}`；stale -> `stale_approval`。drain worker 收 ask_user-caused done -> re-feed input as continuation（对齐 `ga_stdio.py:592-598`）。） -> Step 4: 跑 wire 通过 -> Step 5: Commit `feat(protocol): approval loop + ask_user EngineCap + align wire 4.6 S4`

---

## Phase 7：skill + L1-L4 内存（阶段 7，新 wire）

对齐 tasks.md §9（9.1-9.6）。**新建 wire 测试**（skill 查询 + memory 拼接）。Design Doc §11。

### Task 18: skill/loader.rs + memory.rs（L1-L4 拼接）

对齐 Python `skill_loader.py`（`_get_skills_catalog` mtime 缓存 + `sync_to_l1` + `get_skill_detail` + `parse_provenance` + `validate_skill`）+ `ga.py:407-422` `get_global_memory`。

**Files:**
- Create: `ga-engine/src/skill/loader.rs`
- Create: `ga-engine/src/memory.rs`
- Test: `loader.rs` + `memory.rs`（`#[cfg(test)]`，用 tempfile 模拟 memory/ 目录）

**Interfaces:**
- Consumes: `serde_yaml`（frontmatter）+ `memory/` 目录
- Produces: `SkillStore`（catalog + mtime 缓存 + `discover`/`get_detail`/`sync_to_l1`/`parse_provenance`/`validate_skill`）、`get_global_memory() -> String`（L1+L2+L3 拼接）

- [ ] **Step 1: 写失败测试**（frontmatter 解析 name/description/author/evolvable；mtime 缓存跳过 rescan；sync_to_l1 在 markers 间替换；get_global_memory 拼 L1+L2+structure；validate_skill 结构校验；**与 Python 双向兼容**：Python 写的 L1 Rust 能读，反之亦然） -> Step 2: 跑失败 -> Step 3: 实现（`SkillStore`：`_skill_roots`（`$HOME/.agents/skills` + `cwd/.agents/skills`）；`_scan_dir` 扫 SKILL.md + `serde_yaml::from_str(frontmatter)`；mtime 缓存（`HashMap<path, mtime>` + check 未变跳过）；`sync_to_l1`：在 `<!-- auto-skills-start -->`/`<!-- auto-skills-end -->` 间替换，hot（有 `skill_exp_*.md`）单独行 + cold 逗号分隔。`get_global_memory`：读 L1(`global_mem_insight.txt`)+L2(`global_mem.txt`)+structure 导航拼 prompt。对齐 `skill_loader.py` + `ga.py:407-422`。） -> Step 4: 跑通过 -> Step 5: Commit `feat(skill,memory): loader + L1-L4 memory + bidirectional compat with Python`

### Task 19: skill_query + skill_manage EngineCap（CRUD 部分，不含 distill）

对齐 Python `ga.py:226-239` `do_get_skill_detail` + `tools/skill_manage.py`（CRUD + provenance gate + brief，**不含** distill 触发——那是 Phase 8）。

**Files:**
- Create: `ga-engine/src/skill/manage.rs`（`SkillManage` EngineCap）
- Modify: `ga-engine/src/tools/engine_caps.rs`（`SkillQuery` EngineCap = `get_skill_detail`）
- Test: `manage.rs`（`#[cfg(test)]`）

- [ ] **Step 1: 写失败测试**（create/patch/retire/list_evolvable；provenance gate：user/非 evolvable 只读 -> `protected`；`GA_SKILL_EVOLUTION_ENABLED=1` 门控写动作；list_evolvable/dry_run 豁免；backup .prev；validate 失败 revert） -> Step 2: 跑失败 -> Step 3: 实现（`SkillManage` EngineCap 持 `&Engine` 调 `SkillStore`。action=create: name 不存在 -> makedirs + write SKILL.md -> validate -> 失败删除 -> reset cache + sync_to_l1。patch/retire: provenance gate（author==user 或 !evolvable -> `protected` + suggestion）-> backup_skill_prev -> write -> validate -> 失败 revert_skill_prev + reset_auto_patch_count。对齐 `tools/skill_manage.py`。`SkillQuery` = `get_skill_detail` 转发。**熔断计数器在此引入但 distill 触发在 Phase 8**。） -> Step 4: 跑通过 -> Step 5: Commit `feat(skill): skill_query + skill_manage EngineCap (CRUD + provenance gate)`

### Task 20: 双向兼容验证 + 新 wire 测试

**Files:**
- Create: `ga-engine/tests/wire/test_protocol_skill_memory.py`（新建，对齐 Python `tests/test_skill_loader_l1.py` 结构 + 新增 wire 断言）
- Test: wire `test_protocol_skill_memory.py`

- [ ] **Step 1: 写 wire 失败测试**（mcp/list 风格的 skill 查询消息 + memory 拼接断言；共享 `memory/` 目录 Python 版读写后 Rust 版可读，反之亦然） -> Step 2: 跑失败 -> Step 3: 实现（补 protocol handler `skill/list`+`skill/detail` 消息路由到 `SkillStore`；双向兼容：用 Python 写 L1 后 Rust 读、Rust 写 L1 后 Python 读，断言内容一致） -> Step 4: 跑 wire 通过 -> Step 5: Commit `feat(skill): new wire test for skill query + memory bidirectional compat`

---

## Phase 8：hermes 自进化（阶段 8，新 wire）

对齐 tasks.md §10（10.1-10.6）。**含关键风险：distill worker 编排。** Design Doc §8/§9。

### Task 21: distill worker + channel（DistillJob + 有界 mpsc）★关键风险

对齐 Python `plugins/skill_evolution.py:472-558` `distill` + `_on_agent_after`（post-task 触发 + `SKILL_DISTILL_MIN_TURNS=6` + `GA_SKILL_EVOLUTION_ENABLED` 门控）。Design Doc §8。

**Files:**
- Create: `ga-engine/src/hermes/distill.rs`
- Modify: `ga-engine/src/agent_loop.rs`（agent_after hook 推 DistillJob）
- Test: `distill.rs`（`#[cfg(test)]`）

**Interfaces:**
- Consumes: `Arc<dyn Session>`（独立 session）+ `Arc<SkillStore>` + `mpsc::Receiver<DistillJob>`
- Produces: `DistillJob{history_snapshot, catalog, task_meta}`、`DistillWorker`（常驻 tokio task，rx.recv() -> distill -> Scorer 闸门 -> skill_store 落盘）

- [ ] **Step 1: 写失败测试**（★关键风险）

```rust
#[tokio::test]
async fn distill_worker_non_blocking_post_task() {
    let (tx, rx) = mpsc::channel::<DistillJob>(1);
    let worker = DistillWorker::new(rx, test_scorer(), test_session(), test_store());
    tokio::spawn(worker.run());
    let t0 = Instant::now();
    tx.send(test_job()).await.unwrap();
    assert!(t0.elapsed() < Duration::from_millis(50)); // 不阻塞
}
#[test]
fn distill_guards_min_turns_and_env() {
    // current_turn < 6 -> 不推 DistillJob
    // GA_SKILL_EVOLUTION_ENABLED != 1 -> 不推
}
#[test]
fn distill_job_history_snapshot_is_copy_not_live() {
    // DistillJob.history_snapshot 是 Vec<Message> 拷贝，非 live 引用
}
```

- [ ] **Step 2: 跑失败** -> Step 3: 实现（`DistillJob{history_snapshot: Vec<Message>, catalog: String, task_meta: TaskMeta}`——**history 是拷贝非 live**。`DistillWorker::run()`：循环 `rx.recv()` -> `distill(job)` -> `parse_op` -> `Scorer::score` 闸门 -> `apply_op` 落盘。agent_loop 的 `agent_after` hook（turns>=`SKILL_DISTILL_MIN_TURNS`(6) && `GA_SKILL_EVOLUTION_ENABLED=1` 守卫在推前判）-> `tx.send(job).await` -> **立即返回不等 distill 完成**。有界 channel（容量 16，背压）。独立 session（非 agent_loop 的 session）。不阻塞 agent_loop，无回调，无循环依赖。对齐 `plugins/skill_evolution.py:472-558` + Design Doc §8。） -> Step 4: 跑通过 -> Step 5: Commit `feat(hermes): distill worker + bounded channel + non-blocking post-task (align skill_evolution.py:472-558)`

### Task 22: Scorer trait + InProcessScorer + 阈值闸门 + 熔断

对齐 Python `plugins/skill_evolution.py:44-107`（`VerdictKind`/`Verdict`/`Scorer` Protocol + `SCORE_SCHEMA`）+ `:185-221` `InProcessScorer` + `:411-470` `_parse_op`/`_apply_op`（熔断 `MAX_AUTO_PATCH_PER_SKILL=5`）。Design Doc §9。

**Files:**
- Create: `ga-engine/src/hermes/scorer.rs`
- Test: `scorer.rs`（`#[cfg(test)]`）

**Interfaces:**
- Consumes: `Op`（distill 产出）+ `skill_md` + `history` + `catalog`
- Produces: `Scorer` trait、`InProcessScorer`/`SubagentScorer`、`Verdict{score,verdict,dims,rationale,source}`、熔断计数器

- [ ] **Step 1: 写失败测试**（score>=60 且 verdict=pass 才落盘；reject/revise 或 score<60 -> 不落盘 + brief；单技能连续 auto patch 达 5 -> 只产 Brief 不再 patch；schema 校验失败 -> `ScorerDegraded` -> 退回 InProcessScorer；InProcessScorer 用 serde 强类型反序列化即校验，替代 Python runtime JSON schema） -> Step 2: 跑失败 -> Step 3: 实现（`enum VerdictKind{Pass,Reject,Revise}`；`enum ScorerSource{InProcess,Subagent,Degraded}`；`struct Verdict{score:u8, verdict:VerdictKind, dims:Dims, rationale:String, source:ScorerSource}`；`struct Dims{reusability:u8, verifiedness:u8, non_redundancy:u8}`。`#[async_trait] trait Scorer: Send+Sync { async fn score(&self, op:&Op, skill_md:&str, history:&[Message], catalog:&str) -> Result<Verdict, ScorerDegraded>; }`。`InProcessScorer`：独立 session 调 LLM + `serde` 强类型反序列化 `Verdict`（失败 -> `ScorerDegraded` -> 退回 reject）。阈值闸门 `GA_SKILL_SCORER_THRESHOLD`(60) 在 `parse_op`/`apply_op` 间。熔断 `MAX_AUTO_PATCH_PER_SKILL=5`：`HashMap<name, count>`，patch 达 5 -> 只产 Brief 不落盘。`reset_auto_patch_count`：foreground patch 成功或 scored-pass background patch 清零。对齐 `skill_evolution.py:44-107,185-221,411-470` + Design Doc §9。） -> Step 4: 跑通过 -> Step 5: Commit `feat(hermes): Scorer trait + InProcessScorer + threshold gate + circuit breaker (align skill_evolution.py:44-470)`

### Task 23: 新 wire 测试 + MVP 发版

对齐 tasks.md §10.5-10.6。

**Files:**
- Create: `ga-engine/tests/wire/test_protocol_hermes_distill.py`（新建）
- Test: 全 11 wire + 新 skill/hermes wire

- [ ] **Step 1: 写 wire 失败测试**（post-task distill 触发 + 打分闸门放行/拒绝 + 熔断 5 次后只产 Brief + 不阻塞主循环） -> Step 2: 跑失败 -> Step 3: 实现（补 distill worker 接线到 agent_loop agent_after hook；`GA_SKILL_EVOLUTION_ENABLED=1` 时触发；wire 断言 skill 文件落盘/不落盘） -> Step 4: 跑全 wire 套件（§4.1-4.11 + skill + hermes）确认全绿 -> Step 5: MVP 发版验证：`cargo build --release` 产出单二进制 `ga-engine`，`ls -lh target/release/ga-engine` 断言 <10MB（vs Python PBS bundle 149MB）；运行 `./ga-engine` 在无 Python 环境下完成握手（对齐 `rust-engine` spec "单二进制独立运行"） -> Step 6: Commit `feat(hermes): new wire test + MVP release (stages 0-8 all wire green, single binary <10MB)`

---

## Phase 9-11：MVP 后（简写，留后续）

对齐 tasks.md §11（11.1-11.3）。MVP 后按需推进，每阶段先补对应 wire 测试再实现。

### Task 24: autonomous 自续循环（阶段 9，§4.9 S7）

对齐 Python `ga_stdio.py:619-708` `run_autonomous`（CONTINUATION_PROMPT/BUDGET_LIMIT_PROMPT 重喂 + budget{seconds,turns} + task/done{reason:budget|interrupted}）。
- [ ] **Step 1: 写 wire 失败测试**（对齐 `tests/test_protocol_autonomous.py`：autonomous+budget{seconds:2} -> task/done{reason:budget}；autonomous+budget{turns:99}+interrupt -> task/done{reason:interrupted}）
- [ ] **Step 2: 跑失败** -> **Step 3: 实现**（`run_autonomous(ctx)`：drain iteration 0 -> 循环判 budget 耗尽 -> BUDGET_LIMIT_PROMPT -> done{budget}；interrupt -> done{interrupted}。CONTINUATION_PROMPT/BUDGET_LIMIT_PROMPT 作为 engine 常量，不 import reflect。对齐 `ga_stdio.py:619-708`。） -> **Step 4: 跑 wire 通过** -> **Step 5: Commit** `feat(protocol): autonomous continuation + budget + align wire 4.9 S7`

### Task 25: slash 转发（阶段 10，§4.10 S8/9）

对齐 Python `ga_stdio.py:890-964` `handle_slash_cmd`（injection-class `prompt_for` + state-class raw put_task + /scheduler slash_unsupported + slash/result{task_id,injected_prompt}）。
- [ ] **Step 1: 写 wire 失败测试**（对齐 `tests/test_protocol_slash_forward.py`：/scheduler -> error{slash_unsupported}；/goal -> slash/result{task_id,injected_prompt} + task/delta）
- [ ] **Step 2: 跑失败** -> **Step 3: 实现**（`handle_slash_cmd`：`/scheduler` -> slash_unsupported；`_INJECTION_SLASH_CMDS`(/update / /autorun / /morphling / /goal / /hive / /conductor) -> `prompt_for(cmd,args)` 注入 prompt -> 复用 Task 13 池 -> slash/result{task_id,injected_prompt[:200]}；`_STATE_SLASH_CMDS`(/llm / /resume)+/session.* -> raw put_task -> slash/result{task_id}。**注**：Rust 侧 `prompt_for` 需 Rust 重写或经 MCP server 调用——MVP 后决策。） -> **Step 4: 跑 wire 通过** -> **Step 5: Commit** `feat(protocol): slash/cmd forward + align wire 4.10 S8/9`

### Task 26: 切默认 + Python 退役（阶段 11）

对齐 tasks.md §11.3：全 11 wire + 等价 199 pytest；Python 版退役策略待定。
- [ ] **Step 1: 跑全 11 wire 测试 + 仓内 199 pytest 确认 Rust 引擎全绿** -> **Step 2: 切默认**（前端默认 spawn `ga-engine` 而非 `python -m ga_stdio`，经 durable protocol 无感切换） -> **Step 3: Python 退役决策**（切默认即归档 vs 长期双轨——交用户确认，本计划不锁死） -> **Step 4: Commit** `chore: flip default engine to ga-engine + Python retirement decision`

---

## Self-Review

**1. Spec coverage（逐项核对）：**
- `rust-engine` spec "单二进制 Rust 引擎" -> Task 1（Cargo.toml 无 Python runtime）+ Task 23（<10MB 验证 + 无 Python 握手）✓
- `rust-engine` spec "durable protocol v1 契约遵循（11 wire）" -> Task 2-3(§4.1/4.11)、Task 11(§4.3)、Task 12(§4.5)、Task 13(§4.4)、Task 15(§4.7)、Task 16(§4.8)、Task 17(§4.6)、Task 24(§4.9)、Task 25(§4.10) 全覆盖 ✓
- `rust-engine` spec "tokio async + agent_loop channel 重构（TurnStart+TextChunk）" -> Task 9 ✓
- `rust-engine` spec "llmcore Native-only + 手撸 HTTP（SSE 分片重组 + 16+ header）" -> Task 4-8 ✓
- `rust-engine` spec "MixinSession 故障转移与 spring-back（跨协议混用）" -> Task 8 ✓
- `rust-engine` spec "工具分层 EngineCap+ExternalTool（inline_eval 移除 + queued interrupt I1）" -> Task 10(砍 inline_eval)+Task 11(双 trait)+Task 12(I1 fix) ✓
- `rust-engine` spec "hermes 自进化（distill worker + Scorer + 熔断）" -> Task 21-23 ✓
- `rust-engine` spec "L1-L4 内存文本文件复刻（双向兼容）" -> Task 18+Task 20 ✓
- `tool-dispatch` spec MODIFIED "外部工具=MCP server（drop-in .py 迁移）" -> Task 14 ✓
- `tool-dispatch` spec MODIFIED "双表 dispatch EngineCap 优先" -> Task 11 ✓
- tasks.md §1.3 "inline_eval SOP 全量扫描改写映射" -> Task 10 砍 inline_eval + Task 11 EngineCap 替代（register_done_hook/enter_plan_mode）；**注**：tasks.md §1.3 要求"全量扫描 memory/ 找 inline_eval SOP"，实际 SOP 改写在 Rust 侧由 EngineCap 工具等价达成（spec Scenario "inline_eval SOP 改写后工具等价" 验收），不需改 Python memory/ 文件——双轨期 Python 版 SOP 仍用 Python inline_eval，Rust 版用 EngineCap。已在 Task 10/11 标注。✓
- tasks.md 全 11 组（§1-§11）-> Phase 0(§1+§2)/Phase 1(§3)/Phase 2(§4)/Phase 3(§5)/Phase 4(§6)/Phase 5(§7)/Phase 6(§8)/Phase 7(§9)/Phase 8(§10)/Phase 9-11(§11) ✓

**2. Placeholder 扫描：** 无 TBD/TODO/"implement later"。Phase 9-11（Task 24-26）为简写但每 task 仍有 5 个可执行 step（非占位）。所有 code step 含测试代码或实现骨架 + Python 行号对齐引用。✓

**3. Type 一致性：**
- `AgentEvent` 在 Task 9 定义（`TurnStart{turn}`/`TextChunk(String)`），Task 11/12/17 使用一致 ✓
- `Session` trait 在 Task 6 定义，Task 7/8/21 使用一致 ✓
- `EngineCap`/`ExternalTool` trait 在 Task 11 定义，Task 10(builtin impl ExternalTool)/14(McpTool impl ExternalTool)/17(AskUser impl EngineCap)/19(SkillManage/SkillQuery impl EngineCap) 使用一致 ✓
- `DistillJob`/`DistillWorker` 在 Task 21 定义，Task 22/23 使用一致 ✓
- `Scorer`/`Verdict`/`VerdictKind` 在 Task 22 定义，Task 21/23 使用一致 ✓
- `TaskCtx` 在 Task 12 定义，Task 13(pool) 使用一致 ✓
- `Frame`/`parse_line`/`serialize` 在 Task 1 定义，Task 2-3 使用一致 ✓

**关键风险覆盖：** SSE 手撸（Task 4-5 ★）、MixinSession 状态机（Task 8 ★）、agent_loop channel（Task 9 ★）、distill worker（Task 21 ★）——四个关键风险均重点标注并配 golden snapshot/状态机单测。✓

**计划完整，可交付 subagent-driven-development 逐 task 执行。**
