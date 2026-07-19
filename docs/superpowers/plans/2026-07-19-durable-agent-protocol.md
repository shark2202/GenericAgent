---
change: add-durable-agent-protocol
design-doc: docs/superpowers/specs/2026-07-19-durable-agent-protocol-design.md
base-ref: a5c8eabd1d46e8d300aac39b74a7813317ea450a
---

# Durable Agent Protocol v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务逐个实现。步骤用 checkbox（`- [ ]`）语法跟踪。

**Goal:** 冻结一份 5 年耐久的 agent 协议 v1，作为非 Python 前端（Rust CLI/TUI/GUI，后续 `add-rust-frontends` change）驱动 GenericAgent 的稳定契约面。

**Architecture:** 新增独立 Python 模块 `ga_stdio.py` 作为 stdio bridge 子进程——Rust UI 作父进程 spawn `python -m ga_stdio`，经 newline-delimited JSON 帧双向通信。bridge 内部 `BridgeCore` 持 per-task GA 实例池（有界并发，默认 max 4），每实例跑 `GenericAgent.run()` daemon 线程消费 `task_queue` 排空 `display_queue` 发 `task/delta`；全局注册 `tool_before`/`turn_after` hook 回调，经 `ctx['self'].parent` 或 `ctx['handler'].parent` 反查 GA 再用 pool 反查 task_id 路由 `tool/call`/`tool/result`/`approval/request`。引擎内部（`agent_loop.py` / `ga.py` / `llmcore.py`）零修改，仅复用既有扩展点（`plugins.hooks` 注册、`slash_cmds.prompt_for` 纯函数、`MCPClientManager` 单例）。

**Tech Stack:** Python 3（stdlib + 现有 GA 依赖）、`threading`（每 GA 实例 run 线程 + 独立 stdio reader 线程 + approval Event 同步）、`queue.Queue`（task_queue / display_queue）、`json`（newline-delimited 帧）、`pytest`（单元测试 + 黑盒 wire 测试 spawn 子进程）。

## Global Constraints

每个实现 task 的要求都隐式包含本节——实现者务必先读完再动手。

- **引擎不动**：禁止修改 `agent_loop.py` / `ga.py` / `llmcore.py` 引擎内部。仅复用既有扩展点：`plugins.hooks.register` / `trigger`、`GenericAgent` 公共方法（`put_task` / `abort` / `shutdown` / `next_llm` / `list_llms` / `_handle_slash_cmd`）、`frontends.slash_cmds.prompt_for`、`MCPClientManager.get_instance`。
- **bridge 落点**：所有协议逻辑落在新模块 `ga_stdio.py`（根目录，镜像 `assets/ga_httpapp.py` 先例）。`agentmain.__main__` 的 4 模式分派不动。
- **`--task` / `--func` 文件协议 coexist 不动**：不引入新 CLI flag 到 `agentmain.py`，bridge 由 `python -m ga_stdio` 独立入口启动。
- **reflect 脚本控制不暴露到协议**：autonomous 自续循环由 bridge 内部 owns（复刻 `reflect/goal_mode.py` 的 `CONTINUATION_PROMPT` 模式，不从 reflect import，不 shell out 到 reflect 脚本）。
- **版本与能力协商**：wire `version:"1"`（major only，LSP 式）；每条消息含 `id`（client 分配，server 响应/事件带同 id；server 主动事件用新递增 id）、`type`、`version`。`initialize`/`ready` 协商 `capabilities` 数组。
- **错误韧性**：畸形 JSON 回 `error{code:"bad_json"}` 不断链；未初始化发业务消息回 `error{code:"not_initialized"}`；未知 type 回 `error{code:"unknown_type"}`；child 崩溃由 client 检测 stdout EOF 本地产 `error{code:"child_crashed"}`。
- **测试分层**：实现 task（Task 1-10）用**单元测试** TDD（测 `ga_stdio` 内部纯函数，快，不 spawn 子进程）；Task 11 写 **11 个黑盒 wire 测试**（spawn `python -m ga_stdio` 子进程，断协议面结构非内容）。现有 199 个 pytest 不动不回归。
- **每个 task 结尾**：勾选 `tasks.md` 对应行 再 `git add` + `git commit`（不积攒）。

## File Structure

| 文件 | 职责 | 创建/修改 |
|---|---|---|
| `ga_stdio.py` | bridge 主模块：`BridgeCore`（GA 池 + hook 注册 + stdio 读写循环 + autonomous 自续 + slash 转发 + approval 拦截 + mcp/llm/session 桥接）+ `__main__` 入口 | 新建 |
| `tests/test_ga_stdio_unit.py` | `ga_stdio` 内部纯函数单元测试（帧 parse/serialize、capability 协商、budget 校验、ctx 反查、slash 路由、approval Event） | 新建 |
| `tests/test_protocol_transport.py` | wire 测试 §4.1：握手 + 畸形 JSON 不崩（E2） | 新建 |
| `tests/test_protocol_capability.py` | wire 测试 §4.2：能力不匹配优雅退出 | 新建 |
| `tests/test_protocol_single_task.py` | wire 测试 §4.3：单任务端到端流式（S1） | 新建 |
| `tests/test_protocol_multi_session.py` | wire 测试 §4.4：双任务并发不串台（S2） | 新建 |
| `tests/test_protocol_interrupt.py` | wire 测试 §4.5：中断运行中任务（S3） | 新建 |
| `tests/test_protocol_approval.py` | wire 测试 §4.6：approval 闭环（S4） | 新建 |
| `tests/test_protocol_mcp_visibility.py` | wire 测试 §4.7：MCP 可见性查询（S5） | 新建 |
| `tests/test_protocol_llm_session.py` | wire 测试 §4.8：切模型 + 恢复会话（S6） | 新建 |
| `tests/test_protocol_autonomous.py` | wire 测试 §4.9：autonomous 持续到 budget + 被中断（S7） | 新建 |
| `tests/test_protocol_slash_forward.py` | wire 测试 §4.10：slash 转发注入 + hive 多会话（S8/S9） | 新建 |
| `tests/test_protocol_resilience.py` | wire 测试 §4.11：child 崩溃（E1）+ 未初始化被拒（E3） | 新建 |
| `tests/_protocol_helpers.py` | wire 测试共享 harness：spawn bridge 子进程 + 发/收 JSON 行 + 超时读 | 新建 |

`ga_stdio.py` 内部布局（单文件，但职责内聚于 bridge；模块约 600-800 行可接受，因为全部是协议面而非引擎）：

```
ga_stdio.py
  常量: VERSION="1", SERVER_CAPABILITIES=[...], DEFAULT_MAX_CONCURRENCY=4
  _parse_line(line) -> dict | None        # JSON 解析 + bad_json
  _serialize(msg: dict) -> str           # JSON 序列化 + newline
  _next_id() -> int                      # server 主动事件 id 生成
  _resolve_ga_from_ctx(ctx) -> GA | None  # tool_before 用 self.parent；turn_after 用 handler.parent
  class TaskCtx: ga, dq, task_id, thread, mode, budget, ...
  class BridgeCore:
        pool: {task_id: TaskCtx}
        ga_to_task: {id(GA): task_id}    # hook 回调反查
        semaphore, lock, initialized: bool
        register_hooks()                 # tool_before/turn_after 全局注册一次
        handle_initialize/handle_task_start/handle_task_interrupt/...
        drain_display_queue(task_ctx)    # 排空 dq 发 task/delta + task/done
        run_autonomous(task_ctx, budget)  # 自续循环
        serve()                          # stdio reader 主循环
  if __name__ == '__main__': BridgeCore().serve()
```

## Grounding 事实（已核实源码，实现者必读）

- `GenericAgent` SDK（`agentmain.py:68-246`）：`put_task(query, source, images) -> display_queue`（149-152）；`run()` daemon-able（188-246）；`abort()` 设 `stop_sig`+`handler.code_stop_signal.append(1)`（137-141）；`shutdown()` 停 MCP（143-147）；`next_llm(n=-1)` 切模型并迁移 history（117-127）；`list_llms() -> [(i, name, current_bool)]`（128-130）；`_handle_slash_cmd(raw_query, display_queue)` 处理 `/session.*=`、`/llm [N]`、`/resume`（155-186）；`llmclient.backend.history` 是 session/resume 恢复点（115）。
- `display_queue` item 形状（`agentmain.py:229-235`）：`{'next': str, 'source': str, 'turn': int, 'outputs': list[str]}` 或 `{'done': str, 'source': str, 'turn': int, 'outputs': list[str]}`。except 分支（239）也发 done（带 error 文本）。
- `ga_httpapp.py` 先例（`assets/ga_httpapp.py:10,24-35`）：单 GA + `threading.Thread(target=agent.run, daemon=True).start()` + `dq = agent.put_task(prompt)` + `while 'done' not in (item := dq.get(timeout=2200))` 排空。
- hook 机制（`plugins/hooks.py`）：`register(event)` 装饰器追加回调到 `_registry[event]`；`trigger(event, ctx)` 同步遍历，回调返回 dict 则替换 ctx，per-callback 异常隔离写 stderr；`discover_and_load()` 在 `agentmain.py:12` import 期跑。
- hook ctx 反查（`agent_loop.py:49-68,143`）：`tool_before` 在 `dispatch` 方法内触发，ctx 含 `self`=handler（`GenericAgentHandler`，`ga.py:29-30` 有 `self.parent`=GA）；`turn_after` 在 `agent_runner_loop` 函数内触发（143），ctx 含 `handler`（无 `self`）、`tool_results`（`[{tool_use_id, content}]`，137）、`turn`、`next_prompt`、`exit_reason`。**关键**：两处都要用 `.get()` 容错取 handler，再 `.parent` 取 GA——不硬依赖字段全集。
- `do_ask_user`（`ga.py:76-81`）：`question = args.get("question")`；`candidates = args.get("candidates", [])`；`result = ask_user(question, candidates)`；`return StepOutcome(result, next_prompt="", should_exit=True)`。`should_exit=True` 会让 `agent_runner_loop` break（`agent_loop.py:130`）后 `run()` 发 done。
- `ga_utils.ask_user(question, candidates=None)`（`ga_utils.py:30-33`）：**不阻塞**，直接返回 `{"status":"INTERRUPT","intent":"HUMAN_INTERVENTION","data":{"question":...,"candidates":...}}`。故 approval 注入点 = bridge 启动期 monkey-patch `ga_utils.ask_user`（不动源码，bridge 进程 owns 自己的 namespace）。
- `slash_cmds.prompt_for(cmd, args_text)`（`frontends/slash_cmds.py:597-614`）：纯函数，对 `/update`/`/autorun`/`/morphling`/`/goal`/`/hive`/`/conductor` 返回注入 prompt 字符串；其他返回 `None`。`/llm`/`/session.*`/`/resume` 由 `agentmain._handle_slash_cmd` 内部处理（raw 转发即可）。
- `MCPClientManager`（`mcp_client.py:582-604`）：`get_instance() -> Optional[MCPClientManager]` 单例；实例有 `registry: MCPToolRegistry`（592）；`MCPToolRegistry.get_server_names() -> list[str]`（235）；`MCPToolRegistry._tools: dict[str, list[dict]]`（203，`{server: [{name, description, inputSchema}]}`）——下划线但稳定，bridge 读它构造结构化 `mcp/list` 响应。
- `reflect/goal_mode.py:26-69`：`CONTINUATION_PROMPT` 模板（objective/elapsed/remaining/turn + 创造/检验/改进 3 阶段）；`BUDGET_LIMIT_PROMPT`（55-69）收口轮。bridge 复刻此模式，不从 reflect import。
- 现有测试风格（`tests/test_agent_loop_dispatch.py`）：`_REPO_ROOT` 注入 `sys.path`；用 `_Resp` stand-in 对象；generator 用 `_drain` 跑到 `StopIteration.value`。wire 测试 harness 复用此 repo-root 注入模式。

---

### Task 0a: 推进 `add-selfextract-installer` 出 open 阶段（阻塞型前置）

**类型：** 流程推进（非源码）。**阻塞性：** 高——不完成则后续所有源码写 task（Task 1-11 写 `ga_stdio.py` + `tests/*.py`）会被 comet hook 硬拦截（open 阶段禁写源码）。

**背景：** `add-selfextract-installer` 当前 `.comet.yaml` `phase=open, workflow=full`。open 阶段禁止写源码；本 change 的所有 `ga_stdio.py` 与 `tests/*.py` 落盘会被 hook 拦。必须先把那个 change 推进到至少 `design`（design 也禁写源码）或 `build`。

**Files:**
- 读：`openspec/changes/add-selfextract-installer/.comet.yaml`
- 读：`openspec/changes/add-selfextract-installer/`（proposal/design/tasks 三件套现状）

**Interfaces:**
- Consumes: comet CLI（`comet-state` / `comet-guard` / `comet-handoff` 等脚本，经 `comet-env.mjs` 定位）
- Produces: `add-selfextract-installer` 的 `.comet.yaml.phase` 离开 open（到 design 或 build 均可解除阻塞）

- [ ] **Step 1: 确认 add-selfextract-installer 当前状态**

```bash
cat openspec/changes/add-selfextract-installer/.comet.yaml
ls openspec/changes/add-selfextract-installer/
```

Expected: `phase: open`，`workflow: full`。三件套可能部分缺失（open 阶段正在补）。

- [ ] **Step 2: 按 comet 流程推进该 change——补齐 open 阶段三件套**

若三件套缺失，按 `comet-open` skill 流程补齐 proposal.md / design.md / tasks.md。这不是本 change 的工作，但必须做完。**用户确认点**：open 阶段需求澄清完成确认 + artifact 评审确认——必须暂停等用户明确选择，不得自动跳过。

- [ ] **Step 3: open guard 通过**

```bash
comet-guard add-selfextract-installer open --apply
```

Expected: `ALL CHECKS PASSED`。

- [ ] **Step 4: 阶段推进——进入 design 或直达 build**

```bash
comet-state next add-selfextract-installer
```

按输出：`NEXT: auto` 加载对应 skill（design 走 `/comet-design`）；`NEXT: manual` 按 HINT 手动推进。**目标：phase 离开 open**（到 design 或 build 都可解除本 change 的源码写阻塞）。若进 design，design guard 通过后 `comet-state next` 再推进到 build。

- [ ] **Step 5: 验证阻塞解除**

```bash
grep '^phase:' openspec/changes/add-selfextract-installer/.comet.yaml
```

Expected: `phase: design` 或 `phase: build`（不再是 `open`）。此时本 change 可进入 build 阶段写源码。

- [ ] **Step 6: 勾选 + 提交（本 change 的 tasks.md §1.1）**

勾选 `openspec/changes/add-durable-agent-protocol/tasks.md` 第 1.1 行。提交：

```bash
git add openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "chore(add-durable-agent-protocol): §1.1 unblock — add-selfextract-installer left open phase"
```

---

### Task 0b: 确认 `hermes-isolated-skill-scorer` 无冲突（无冲突打勾）

**类型：** 核实（非源码）。**阻塞性：** 无——已预核实。

- [ ] **Step 1: 确认无 .comet.yaml（非 comet 托管）**

```bash
ls openspec/changes/hermes-isolated-skill-scorer/.comet.yaml 2>/dev/null && echo EXISTS || echo NO-COMET
```

Expected: `NO-COMET`（该 change 非 comet 托管，不构成源码写阻塞）。

- [ ] **Step 2: 勾选 + 提交（本 change 的 tasks.md §1.2）**

勾选 tasks.md 第 1.2 行。提交：

```bash
git add openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "chore(add-durable-agent-protocol): §1.2 confirm hermes-isolated-skill-scorer no conflict"
```

---

### Task 1: ga_stdio.py 骨架——stdio 读写循环 + initialize/ready 握手 + 畸形 JSON error（E2/E3）

**覆盖：** Design Doc §10.1 / tasks.md §3.1, §3.2, §3.11（部分）/ S-传输 / E2 / E3。

**Files:**
- Create: `ga_stdio.py`
- Test: `tests/test_ga_stdio_unit.py`（本 task 仅测帧 parse/serialize + capability 协商 + bad_json/not_initialized 路由）

**Interfaces:**
- Consumes: 无前置 task 依赖（Task 0a/0b 是流程前置）。
- Produces: `ga_stdio._parse_line(line)`、`ga_stdio._serialize(msg)`、`ga_stdio.VERSION`、`ga_stdio.SERVER_CAPABILITIES`、`ga_stdio.BridgeCore`（本 task 仅骨架：`serve()` 跑 stdio 循环 + `handle_initialize` + 错误路由；不处理业务消息）。

- [x] **Step 1: 写失败测试——帧 parse/serialize + VERSION/caps**

`tests/test_ga_stdio_unit.py`：

```python
"""ga_stdio internal-function unit tests (protocol layer, no subprocess spawn)."""
import json
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import ga_stdio  # noqa: E402


def test_serialize_roundtrip():
    msg = {"id": 7, "type": "ready", "version": "1", "capabilities": ["streaming"]}
    line = ga_stdio._serialize(msg)
    assert line.endswith("\n")
    parsed = json.loads(line)
    assert parsed == msg


def test_parse_line_valid_json():
    line = '{"id": 1, "type": "initialize", "version": "1", "capabilities": []}\n'
    msg = ga_stdio._parse_line(line)
    assert msg is not None
    assert msg["type"] == "initialize"
    assert msg["version"] == "1"


def test_parse_line_bad_json_returns_none():
    """Malformed JSON line returns None so caller can emit bad_json error (E2)."""
    assert ga_stdio._parse_line("not json at all\n") is None
    assert ga_stdio._parse_line('{"id": 1, "type":}\n') is None


def test_version_is_major_only_string_one():
    assert ga_stdio.VERSION == "1"


def test_server_capabilities_include_all_v1_caps():
    expected = {"streaming", "multi-session", "autonomous", "approval",
                "mcp", "slash", "llm-switch", "session-resume"}
    assert expected.issubset(set(ga_stdio.SERVER_CAPABILITIES))
```

- [x] **Step 2: 跑测试确认失败**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ga_stdio'`。

- [x] **Step 3: 实现 ga_stdio.py 骨架**

`ga_stdio.py`：

```python
"""Durable Agent Protocol v1 — stdio bridge.

Spawned by a non-Python frontend (Rust CLI/TUI/GUI) as a child process:
    python -m ga_stdio
Communicates via newline-delimited JSON on stdin/stdout. stderr is for
diagnostics only (not part of the protocol).

Design: docs/superpowers/specs/2026-07-19-durable-agent-protocol-design.md
Engine internals (agent_loop/ga/llmcore) are untouched; this module only
reuses existing extension points (plugins.hooks, GenericAgent SDK,
slash_cmds.prompt_for, MCPClientManager).
"""
import sys
import os
import json
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERSION = "1"  # major only, LSP-style; breaking change bumps major
SERVER_CAPABILITIES = [
    "streaming", "multi-session", "autonomous", "approval",
    "mcp", "slash", "llm-switch", "session-resume",
]


def _parse_line(line):
    """Parse one stdin line into a dict, or None if malformed (E2)."""
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(msg, dict):
        return None
    return msg


def _serialize(msg):
    """Serialize one message dict to a JSON line (with trailing newline)."""
    return json.dumps(msg, ensure_ascii=False) + "\n"


class BridgeCore:
    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._write_lock = threading.Lock()
        self._next_event_id = 1
        self.initialized = False
        # pool / hooks / semaphore wired in later tasks

    def _next_id(self):
        i = self._next_event_id
        self._next_event_id += 1
        return i

    def send(self, msg):
        """Write one JSON line to stdout (thread-safe)."""
        line = _serialize(msg)
        with self._write_lock:
            self.stdout.write(line)
            self.stdout.flush()

    def send_error(self, code, message, original_id=None):
        err = {"id": self._next_id(), "type": "error", "version": VERSION,
               "code": code, "message": message}
        if original_id is not None:
            err["original_id"] = original_id
        self.send(err)

    def handle_initialize(self, msg):
        caps = msg.get("capabilities", [])
        missing = [c for c in caps if c not in SERVER_CAPABILITIES]
        if missing:
            self.send_error("capability_unsupported",
                            f"server lacks: {missing}", original_id=msg.get("id"))
            return
        self.initialized = True
        # agent_info wired in Task 6 (needs GA instance); stub for now
        self.send({"id": msg["id"], "type": "ready", "version": VERSION,
                   "capabilities": SERVER_CAPABILITIES,
                   "agent_info": {"name": "GenericAgent", "mcp_connected": 0, "llm_count": 0}})

    def dispatch(self, msg):
        """Route one parsed client message. Business messages require initialized."""
        mtype = msg.get("type")
        if mtype == "initialize":
            self.handle_initialize(msg)
            return
        if not self.initialized:
            self.send_error("not_initialized",
                            f"got {mtype!r} before initialize (E3)",
                            original_id=msg.get("id"))
            return
        # Business handlers wired in later tasks:
        if mtype == "task/start":
            self.send_error("unknown_type", "task/start not implemented yet",
                            original_id=msg.get("id"))
        elif mtype == "llm/list":
            self.send_error("unknown_type", "llm/list not implemented yet",
                            original_id=msg.get("id"))
        elif mtype == "mcp/list":
            self.send_error("unknown_type", "mcp/list not implemented yet",
                            original_id=msg.get("id"))
        elif mtype == "slash/cmd":
            self.send_error("unknown_type", "slash/cmd not implemented yet",
                            original_id=msg.get("id"))
        else:
            self.send_error("unknown_type", f"unknown type {mtype!r}",
                            original_id=msg.get("id"))

    def serve(self):
        """Main stdio reader loop. One JSON line per stdin line."""
        for line in self.stdin:
            msg = _parse_line(line)
            if msg is None:
                # E2: malformed JSON — emit error, do NOT break the loop
                self.send_error("bad_json", f"unparseable line: {line.strip()[:80]}",
                                original_id=None)
                continue
            try:
                self.dispatch(msg)
            except Exception as e:
                self.send_error("internal_error", f"{type(e).__name__}: {e}",
                                original_id=msg.get("id"))
        # stdin EOF — child process exits; client detects via stdout EOF (E1)


if __name__ == "__main__":
    BridgeCore().serve()
```

- [x] **Step 4: 跑单元测试确认通过**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: PASS（5 个测试全过）。

- [x] **Step 5: 手动 smoke——握手 + 畸形 JSON**

```bash
cd D:/GenericAgent && printf '{"id":1,"type":"initialize","version":"1","capabilities":[]}\nnot json\n{"id":2,"type":"task/start","prompt":"hi"}\n' | python -m ga_stdio
```

Expected stdout（逐行）：
1. `{"id":1,"type":"ready","version":"1","capabilities":[...],"agent_info":{...}}`
2. `{"id":N,"type":"error","version":"1","code":"bad_json",...,"original_id":null}`
3. `{"id":N,"type":"error","version":"1","code":"unknown_type","message":"task/start not implemented yet",...}`

- [x] **Step 6: 勾选 + 提交**

勾选 tasks.md §3.1、§3.2、§3.11（部分）。

```bash
git add ga_stdio.py tests/test_ga_stdio_unit.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "feat(ga_stdio): skeleton — stdio loop + initialize/ready + bad_json/not_initialized (E2/E3)"
```

---

### Task 2: 单 task 流式——put_task → display_queue 排空 → task/delta + task/done（S1）

**覆盖：** Design Doc §10.2 / tasks.md §3.3（部分）/ S1。

**Files:**
- Modify: `ga_stdio.py`（`handle_task_start` 单 task 路径 + `drain_display_queue` + `TaskCtx` 类）
- Modify: `tests/test_ga_stdio_unit.py`（加 `drain_display_queue` 单元测试用 fake dq）

**Interfaces:**
- Consumes: `ga_stdio.BridgeCore`（Task 1）、`agentmain.GenericAgent`（`put_task` / `run` / `display_queue` item 形状）。
- Produces: `ga_stdio.TaskCtx`（数据类：`ga`, `dq`, `task_id`, `thread`）、`BridgeCore.handle_task_start`（单 task，不并发——并发在 Task 6）、`BridgeCore.drain_display_queue(task_ctx)`（排空 dq 发 task/delta + task/done）。

- [x] **Step 1: 写失败测试——drain_display_queue 用 fake dq**

追加到 `tests/test_ga_stdio_unit.py`：

```python
import queue


def test_drain_display_queue_emits_delta_then_done():
    """drain turns {next,done} display_queue items into task/delta + task/done."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    dq = queue.Queue()
    dq.put({"next": "hello", "source": "user", "turn": 1, "outputs": ["hello"]})
    dq.put({"done": "hello world", "source": "user", "turn": 1, "outputs": ["hello world"]})
    sent = []
    core.send = lambda m: sent.append(m)  # capture
    task_ctx = ga_stdio.TaskCtx(ga=None, dq=dq, task_id="t1", thread=None)
    core.drain_display_queue(task_ctx)
    types = [m["type"] for m in sent]
    assert "task/delta" in types
    assert types[-1] == "task/done"
    done_msg = sent[-1]
    assert done_msg["task_id"] == "t1"
    assert done_msg["reason"] == "completed"
    assert done_msg["version"] == "1"
    assert "turn" in done_msg


def test_drain_display_queue_error_done_emits_error_reason():
    """A done carrying an error-shaped payload → task/done{reason:error}."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    dq = queue.Queue()
    # agentmain except branch appends a fenced error block to done text (agentmain.py:239)
    dq.put({"done": "partial\n```\nValueError: boom @ file:1\n```", "source": "user",
            "turn": 0, "outputs": []})
    sent = []
    core.send = lambda m: sent.append(m)
    core.drain_display_queue(ga_stdio.TaskCtx(ga=None, dq=dq, task_id="t9", thread=None))
    assert sent[-1]["type"] == "task/done"
    assert sent[-1]["reason"] == "error"
    assert sent[-1].get("error")
```

- [x] **Step 2: 跑测试确认失败**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: FAIL — `AttributeError: module 'ga_stdio' has no attribute 'TaskCtx'`（及 `drain_display_queue`）。

- [x] **Step 3: 实现 TaskCtx + handle_task_start + drain_display_queue**

在 `ga_stdio.py` 顶部 import 区加（在 `import threading` 后）：

```python
import queue
import time
```

在 `BridgeCore` 类**之前**加 `TaskCtx` 数据类：

```python
class TaskCtx:
    """Per-task runtime: one GenericAgent instance + its display_queue + control."""
    def __init__(self, ga, dq, task_id, thread, mode="single", budget=None):
        self.ga = ga
        self.dq = dq
        self.task_id = task_id
        self.thread = thread
        self.mode = mode            # "single" | "autonomous"
        self.budget = budget        # {seconds?, turns?} for autonomous; None for single
        self.start_time = time.time()
        self.turns_used = 0
        self.interrupted = False
        self._objective = None
        self.pending_approval = False
        self._last_approval_input_ref = None
        self._approval_input = None
```

在 `BridgeCore.__init__` 加（在 `self.initialized = False` 后）：

```python
        self.pool = {}                  # {task_id: TaskCtx}
        self.ga_to_task = {}            # {id(GA): task_id} for hook routing
        self._task_counter = 0
        self._tool_id_counter = 0
        self._pool_lock = threading.Lock()
        self._hooks_registered = False
```

在 `BridgeCore` 内加方法（并替换 Task 1 dispatch 里 `task/start` 占位分支为 `self.handle_task_start(msg)`）：

```python
    def _new_task_id(self):
        self._task_counter += 1
        return f"t{self._task_counter}"

    def handle_task_start(self, msg):
        prompt = msg.get("prompt", "")
        mode = msg.get("mode", "single")
        budget = msg.get("budget")
        images = msg.get("images", [])
        task_id = self._new_task_id()
        ga = self._spawn_ga()           # Task 6 promotes this to a pool slot
        dq = ga.put_task(prompt, source="stdio", images=images)
        ctx = TaskCtx(ga=ga, dq=dq, task_id=task_id, thread=None, mode=mode, budget=budget)
        ctx._objective = prompt
        with self._pool_lock:
            self.pool[task_id] = ctx
            self.ga_to_task[id(ga)] = task_id
        self.send({"id": msg["id"], "type": "task/ack", "version": VERSION,
                   "task_id": task_id, "status": "running"})
        t = threading.Thread(target=self._run_task, args=(ctx,), daemon=True)
        ctx.thread = t
        t.start()

    def _spawn_ga(self):
        """Create a fresh GenericAgent + run() daemon thread. Task 6 wraps pool/sem."""
        from agentmain import GenericAgent
        ga = GenericAgent()
        ga.verbose = False
        ga.inc_out = True
        threading.Thread(target=ga.run, daemon=True).start()
        return ga

    def _run_task(self, ctx):
        """Worker: drain display_queue for one task until done/interrupted."""
        try:
            if ctx.mode == "autonomous":
                self.run_autonomous(ctx)      # wired in Task 7
            else:
                self.drain_display_queue(ctx)
        except Exception as e:
            self.send({"id": self._next_id(), "type": "task/done", "version": VERSION,
                       "task_id": ctx.task_id, "reason": "error",
                       "error": f"{type(e).__name__}: {e}"})
        finally:
            self._release_task(ctx)

    def _release_task(self, ctx):
        with self._pool_lock:
            self.pool.pop(ctx.task_id, None)
            if ctx.ga is not None:
                self.ga_to_task.pop(id(ctx.ga), None)
        try:
            ctx.ga.shutdown()
        except Exception:
            pass

    def drain_display_queue(self, ctx):
        """Drain display_queue → emit task/delta + task/done. Used by single mode."""
        while True:
            try:
                item = ctx.dq.get(timeout=2200)
            except queue.Empty:
                self.send({"id": self._next_id(), "type": "task/done", "version": VERSION,
                           "task_id": ctx.task_id, "reason": "error",
                           "error": "display_queue timeout (2200s)"})
                return
            if "next" in item:
                self.send({"id": self._next_id(), "type": "task/delta", "version": VERSION,
                           "task_id": ctx.task_id, "content": item.get("next", ""),
                           "turn": item.get("turn", 0)})
                continue
            if "done" in item:
                done_text = item.get("done", "")
                # agentmain except branch (agentmain.py:239) appends ```\n{format_error}\n```
                is_error = False
                if "```" in done_text:
                    parts = done_text.split("```")
                    if len(parts) >= 3 and "Error" in parts[-2]:
                        is_error = True
                reason = "error" if is_error else ("interrupted" if ctx.interrupted else "completed")
                done_msg = {"id": self._next_id(), "type": "task/done", "version": VERSION,
                            "task_id": ctx.task_id, "reason": reason,
                            "turn": item.get("turn", 0)}
                if reason == "error":
                    done_msg["error"] = done_text
                self.send(done_msg)
                return

    def run_autonomous(self, ctx):
        """Placeholder — wired in Task 7."""
        raise NotImplementedError("autonomous wired in Task 7")
```

替换 `dispatch` 中 `task/start` 分支为：

```python
        if mtype == "task/start":
            self.handle_task_start(msg)
```

- [x] **Step 4: 跑单元测试确认通过**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: PASS（7 个测试全过）。

- [x] **Step 5: 勾选 + 提交**

勾选 tasks.md §3.3。

```bash
git add ga_stdio.py tests/test_ga_stdio_unit.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "feat(ga_stdio): single-task streaming — put_task→drain display_queue→task/delta+task/done (S1)"
```

---

### Task 3: hook 注册——tool_before→tool/call、turn_after→tool/result（含 ctx 字段表驱动）

**覆盖：** Design Doc §10.3 / tasks.md §3.3（tool 事件部分）/ Q3。

**Files:**
- Modify: `ga_stdio.py`（`register_hooks` + `_resolve_ga_from_ctx` + tool_before/turn_after 回调）
- Modify: `tests/test_ga_stdio_unit.py`（ctx 反查 + tool/event 序列化单元测试）

**Interfaces:**
- Consumes: `plugins.hooks.register` / `trigger`（`plugins/hooks.py`）、`GenericAgentHandler.parent`（`ga.py:30`）。
- Produces: `ga_stdio._resolve_ga_from_ctx(ctx)`、`BridgeCore.register_hooks()`。

- [x] **Step 1: 写失败测试——ctx 反查 + tool/call 序列化**

追加到 `tests/test_ga_stdio_unit.py`：

```python
class _FakeGA:
    """Stand-in for GenericAgent so we can test ga_to_task reverse-lookup."""
    pass


class _FakeHandler:
    def __init__(self, parent):
        self.parent = parent


def test_resolve_ga_from_tool_before_ctx():
    """tool_before ctx has `self` = handler; GA = handler.parent."""
    ga = _FakeGA()
    handler = _FakeHandler(ga)
    ctx = {"self": handler, "tool_name": "code_run", "args": {"x": 1}}
    assert ga_stdio._resolve_ga_from_ctx(ctx) is ga


def test_resolve_ga_from_turn_after_ctx():
    """turn_after ctx has `handler` (no `self`); GA = handler.parent."""
    ga = _FakeGA()
    handler = _FakeHandler(ga)
    ctx = {"handler": handler, "tool_results": [], "turn": 1}
    assert ga_stdio._resolve_ga_from_ctx(ctx) is ga


def test_resolve_ga_returns_none_when_missing():
    """Resilience: missing handler/self → None (don't crash the hook)."""
    assert ga_stdio._resolve_ga_from_ctx({"tool_name": "x"}) is None
    assert ga_stdio._resolve_ga_from_ctx({}) is None


def test_tool_call_serialization_has_required_fields():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)
    core._emit_tool_call(task_id="t3", tool_id="tool_1", name="code_run", args={"x": 1})
    m = sent[-1]
    assert m["type"] == "tool/call"
    assert m["task_id"] == "t3"
    assert m["tool_id"] == "tool_1"
    assert m["name"] == "code_run"
    assert m["args"] == {"x": 1}
    assert m["version"] == "1"
    assert "id" in m


def test_tool_result_from_turn_after_ctx():
    """turn_after callback walks tool_results list → one tool/result per entry."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGA()
    handler = _FakeHandler(ga)
    with core._pool_lock:
        core.ga_to_task[id(ga)] = "t5"
    sent = []
    core.send = lambda m: sent.append(m)
    tool_results = [{"tool_use_id": "tu_1", "content": "ok"},
                    {"tool_use_id": "tu_2", "content": "fail"}]
    ctx = {"handler": handler, "tool_results": tool_results, "turn": 3}
    core._on_turn_after(ctx)
    results = [m for m in sent if m["type"] == "tool/result"]
    assert len(results) == 2
    assert results[0]["tool_id"] == "tu_1"
    assert results[0]["content"] == "ok"
    assert results[0]["task_id"] == "t5"
    assert results[1]["tool_id"] == "tu_2"
```

- [x] **Step 2: 跑测试确认失败**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: FAIL — `AttributeError: module 'ga_stdio' has no attribute '_resolve_ga_from_ctx'`。

- [x] **Step 3: 实现 hook 注册 + ctx 反查 + tool/call+result 回调**

在模块级（`BridgeCore` 类前）加反查函数：

```python
def _resolve_ga_from_ctx(ctx):
    """Recover the GenericAgent instance from a hook ctx dict.

    tool_before fires inside BaseHandler.dispatch (agent_loop.py:53/63) —
    ctx carries `self` = handler, GA = handler.parent (ga.py:30).
    turn_after fires inside agent_runner_loop (agent_loop.py:143) — ctx has
    no `self`, only `handler`; GA = handler.parent.
    Uses .get() so field drift in agent_loop doesn't crash the callback.
    """
    handler = ctx.get("self") or ctx.get("handler")
    return getattr(handler, "parent", None)
```

在 `BridgeCore` 内加方法：

```python
    def register_hooks(self):
        """Register tool_before/turn_after callbacks once at bridge startup.

        Coexists with existing plugin callbacks (langfuse_tracing, skill_evolution):
        plugins.hooks.trigger calls every registered callback (plugins/hooks.py:18).
        """
        if self._hooks_registered:
            return
        self._hooks_registered = True
        from plugins.hooks import register

        @register("tool_before")
        def _tool_before(ctx):
            ga = _resolve_ga_from_ctx(ctx)
            if ga is None:
                return
            with self._pool_lock:
                task_id = self.ga_to_task.get(id(ga))
            if task_id is None:
                return
            tool_name = ctx.get("tool_name", "?")
            args = dict(ctx.get("args", {}) or {})
            args.pop("_index", None)     # internal keys injected by dispatch
            args.pop("_tool_num", None)
            self._tool_id_counter += 1
            tool_id = f"tool_{self._tool_id_counter}"
            if tool_name == "ask_user":
                # ask_user is intercepted by the approval path (Task 5), not tool/call
                self._on_approval_request(task_id, tool_id, args)
                return
            self._emit_tool_call(task_id=task_id, tool_id=tool_id,
                                 name=tool_name, args=args)

        @register("turn_after")
        def _turn_after(ctx):
            ga = _resolve_ga_from_ctx(ctx)
            if ga is None:
                return
            with self._pool_lock:
                task_id = self.ga_to_task.get(id(ga))
            if task_id is None:
                return
            self._on_turn_after(ctx, task_id)

    def _emit_tool_call(self, task_id, tool_id, name, args):
        self.send({"id": self._next_id(), "type": "tool/call", "version": VERSION,
                   "tool_id": tool_id, "task_id": task_id, "name": name, "args": args})

    def _on_turn_after(self, ctx, task_id):
        """Walk tool_results list → one tool/result per entry (agent_loop.py:137)."""
        tool_results = ctx.get("tool_results", []) or []
        for tr in tool_results:
            tool_use_id = tr.get("tool_use_id", "")
            content = tr.get("content", "")
            self.send({"id": self._next_id(), "type": "tool/result", "version": VERSION,
                       "tool_id": tool_use_id, "task_id": task_id, "content": content})

    def _on_approval_request(self, task_id, tool_id, args):
        """Wired in Task 5. Stub here so tool_before doesn't crash on ask_user."""
        pass
```

在 `serve()` 的 `for line in self.stdin:` **之前**调用 `register_hooks()`：

```python
    def serve(self):
        self.register_hooks()
        for line in self.stdin:
            msg = _parse_line(line)
            ...
```

（`ga_to_task` 反查已在 Task 2 的 `handle_task_start` 里 `with self._pool_lock` 块内写入；`_release_task` 里 pop——Task 2 已包含。）

- [x] **Step 4: 跑单元测试确认通过**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: PASS（12 个测试全过）。

- [x] **Step 5: 勾选 + 提交**

勾选 tasks.md §3.3（tool 事件部分）。

```bash
git add ga_stdio.py tests/test_ga_stdio_unit.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "feat(ga_stdio): hook routing — tool_before→tool/call, turn_after→tool/result (Q3)"
```

---

### Task 4: task/interrupt → abort → task/done{interrupted}（S3）

**覆盖：** Design Doc §10.4 / tasks.md §3.5 / S3。

**Files:**
- Modify: `ga_stdio.py`（`handle_task_interrupt` + drain 检测中断）
- Modify: `tests/test_ga_stdio_unit.py`（interrupt 单元测试用 fake ga）

**Interfaces:**
- Consumes: `GenericAgent.abort()`（`agentmain.py:137-141`）、`TaskCtx.interrupted` 标志。
- Produces: `BridgeCore.handle_task_interrupt`。

- [ ] **Step 1: 写失败测试——interrupt 设标志 + abort**

追加到 `tests/test_ga_stdio_unit.py`：

```python
class _FakeGAWithAbort:
    def __init__(self):
        self.aborted = False
    def abort(self):
        self.aborted = True
    def shutdown(self):
        pass


def test_handle_task_interrupt_calls_abort_and_sets_flag():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithAbort()
    ctx = ga_stdio.TaskCtx(ga=ga, dq=queue.Queue(), task_id="t7", thread=None)
    with core._pool_lock:
        core.pool["t7"] = ctx
        core.ga_to_task[id(ga)] = "t7"
    core.handle_task_interrupt({"id": 100, "type": "task/interrupt", "task_id": "t7"})
    assert ga.aborted is True
    assert ctx.interrupted is True


def test_handle_task_interrupt_unknown_task_id_emits_error():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_task_interrupt({"id": 100, "type": "task/interrupt", "task_id": "nope"})
    assert sent[-1]["type"] == "error"
    assert sent[-1]["code"] == "unknown_task"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: FAIL — `AttributeError: 'BridgeCore' object has no attribute 'handle_task_interrupt'`。

- [ ] **Step 3: 实现 handle_task_interrupt**

在 `BridgeCore` 内加方法：

```python
    def handle_task_interrupt(self, msg):
        task_id = msg.get("task_id")
        with self._pool_lock:
            ctx = self.pool.get(task_id)
        if ctx is None:
            self.send_error("unknown_task", f"no running task {task_id!r}",
                            original_id=msg.get("id"))
            return
        ctx.interrupted = True
        try:
            ctx.ga.abort()      # agentmain.py:137-141: stop_sig + handler.code_stop_signal
        except Exception as e:
            self.send_error("interrupt_failed", f"{type(e).__name__}: {e}",
                            original_id=msg.get("id"))
            return
        # No immediate task/done — the drain loop emits task/done{reason:interrupted}
        # when run()'s display_queue done arrives (stop_sig makes run() break → done).
        self.send({"id": msg["id"], "type": "task/ack", "version": VERSION,
                   "task_id": task_id, "status": "interrupting"})
```

在 `dispatch` 的 business 分支里（`task/start` 后）加：

```python
        elif mtype == "task/interrupt":
            self.handle_task_interrupt(msg)
```

（`drain_display_queue` 在 Task 2 已用 `ctx.interrupted` 决定 `reason`——interrupted 标志设后，下一次 done 走 `reason="interrupted"` 分支。）

- [ ] **Step 4: 跑单元测试确认通过**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: PASS（14 个测试全过）。

- [ ] **Step 5: 勾选 + 提交**

勾选 tasks.md §3.5。

```bash
git add ga_stdio.py tests/test_ga_stdio_unit.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "feat(ga_stdio): task/interrupt→abort→task/done{reason:interrupted} (S3)"
```

---

### Task 5: approval 拦截 ask_user + 线程模型（S4）—— spike 已定注入点

**覆盖：** Design Doc §10.5 / §6.4 / tasks.md §3.7 / S4。

**Spike 结论（已核实源码，写入实现，不再做探索性 spike）：**
- `ga_utils.ask_user(question, candidates=None)`（`ga_utils.py:30-33`）**不阻塞**，返回 `{"status":"INTERRUPT","intent":"HUMAN_INTERVENTION","data":{...}}` dict。
- `do_ask_user`（`ga.py:76-81`）调它，返回 `StepOutcome(result, next_prompt="", should_exit=True)` → `agent_runner_loop` break（`agent_loop.py:130`）→ `run()` 发 done。
- **注入点**：bridge 启动期 monkey-patch `ga_utils.ask_user`（**不动源码**，bridge 进程 owns 自己的 namespace）。patch 后的 `ask_user` 阻塞当前 agent 线程等 `approval/response`，返回 client 的 `input`（或 reject 语义 dict）。
- `do_ask_user` 的 `should_exit=True` 会让 agent loop 退出 → `run()` done。bridge 的 `drain_display_queue` 拿到 done 时，检查 `TaskCtx.pending_approval` 标志：若为 True（本次 done 是 ask_user 引发的）→ **不发 task/done**，而是把 client input 作为新 prompt 重新 `put_task` 进**同一个 GA 实例**（history 保留），继续 drain；task_id 不变、对 client 隐藏 ask_user 的 task 边界。

**Files:**
- Modify: `ga_stdio.py`（`_patch_ask_user` + `_on_approval_request` + `handle_approval_response` + drain 的 approval 续跑分支）
- Modify: `tests/test_ga_stdio_unit.py`（approval Event 同步单元测试）

**Interfaces:**
- Consumes: `ga_utils.ask_user`（patch 目标）、`threading.Event`。
- Produces: `BridgeCore.handle_approval_response`、`BridgeCore._pending_approvals`（`{(task_id, tool_id): (Event, box)}`）。

- [ ] **Step 1: 写失败测试——approval request/response 同步**

追加到 `tests/test_ga_stdio_unit.py`：

```python
def test_approval_request_emits_and_response_wakes():
    """_on_approval_request stamps GA + sets pending flag; the patched ask_user
    (called by do_ask_user on the same agent thread) emits approval/request and
    blocks; handle_approval_response fills the box and sets the Event."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithAbort()
    ctx = ga_stdio.TaskCtx(ga=ga, dq=queue.Queue(), task_id="t1", thread=None)
    with core._pool_lock:
        core.pool["t1"] = ctx
        core.ga_to_task[id(ga)] = "t1"
    sent = []
    core.send = lambda m: sent.append(m)
    core._patch_ask_user()
    core._bridge_ask_user._current_ga = ga
    # _on_approval_request only stamps GA + flag; the actual approval/request
    # is emitted by the patched ask_user when do_ask_user calls it. Simulate that:
    core._on_approval_request(task_id="t1", tool_id="tool_1",
                              args={"question": "continue?", "candidates": ["y", "n"]})
    assert ctx.pending_approval is True
    # simulate do_ask_user calling our patched ask_user (on the agent thread):
    import threading as _th
    box = {}
    th = _th.Thread(target=lambda: box.update({"ret": core._bridge_ask_user("continue?", ["y", "n"]}),
                  daemon=True)
    th.start()
    # the patched ask_user should have emitted approval/request and be blocking
    import time as _t; _t.sleep(0.05)
    reqs = [m for m in sent if m["type"] == "approval/request"]
    assert len(reqs) == 1
    assert reqs[0]["task_id"] == "t1"
    assert reqs[0]["prompt"] == "continue?"
    assert reqs[0]["options"] == ["y", "n"]
    # client responds → handle_approval_response wakes the blocked thread
    tool_id = reqs[0]["tool_id"]
    core.handle_approval_response({"id": 200, "type": "approval/response",
                                   "task_id": "t1", "tool_id": tool_id,
                                   "decision": "approve", "input": "yes please"})
    th.join(timeout=2)
    assert not th.is_alive()
    assert box["ret"] == "yes please"
    assert ctx._approval_input == "yes please"


def test_approval_response_unknown_slot_emits_stale_approval():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_approval_response({"id": 200, "type": "approval/response",
                                   "task_id": "ghost", "tool_id": "tool_x",
                                   "decision": "approve"})
    assert sent[-1]["type"] == "error"
    assert sent[-1]["code"] == "stale_approval"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: FAIL — `_on_approval_request` 是 stub（Task 3 占位），`_patch_ask_user` 不存在。

- [ ] **Step 3: 实现 approval 拦截 + patch ask_user + drain 续跑**

在 `BridgeCore.__init__` 加（`self._hooks_registered = False` 后）：

```python
        self._pending_approvals = {}   # {(task_id, tool_id): (Event, box)}
        self._ask_user_patched = False
```

在 `BridgeCore` 内加方法：

```python
    def _patch_ask_user(self):
        """Monkey-patch ga_utils.ask_user so do_ask_user calls our bridge version.

        NOT a source edit to ga_utils.py — bridge owns its own process namespace.
        ga_utils.ask_user(question, candidates=None) currently returns a non-blocking
        INTERRUPT dict (ga_utils.py:30-33); we replace it with a blocking call that
        waits on the approval response from the stdio reader thread, then returns
        the client's input so do_ask_user's StepOutcome carries it.
        """
        if self._ask_user_patched:
            return
        self._ask_user_patched = True
        import ga_utils

        def _bridge_ask_user(question, candidates=None):
            """Blocking: emit approval/request, wait for approval/response, return input."""
            ga = _bridge_ask_user._current_ga
            with self._pool_lock:
                task_id = self.ga_to_task.get(id(ga)) if ga is not None else None
            if task_id is None:
                # GA not in pool — fall back to old non-blocking behavior
                return {"status": "INTERRUPT", "intent": "HUMAN_INTERVENTION",
                        "data": {"question": question, "candidates": candidates or []}}
            self._tool_id_counter += 1
            tool_id = f"ask_{self._tool_id_counter}"
            ev = threading.Event()
            box = {}
            self._pending_approvals[(task_id, tool_id)] = (ev, box)
            self.send({"id": self._next_id(), "type": "approval/request", "version": VERSION,
                       "task_id": task_id, "tool_id": tool_id,
                       "prompt": question, "options": candidates or []})
            ev.wait()  # blocks the agent thread until approval/response arrives
            decision = box.get("decision", "reject")
            if decision == "approve":
                return box.get("input", "")   # becomes do_ask_user's StepOutcome.data
            return {"status": "REJECTED", "data": {"question": question}}

        _bridge_ask_user._current_ga = None
        ga_utils.ask_user = _bridge_ask_user
        self._bridge_ask_user = _bridge_ask_user

    def _on_approval_request(self, task_id, tool_id, args):
        """tool_before callback for ask_user: stamp the current GA onto the patched
        ask_user so it can route, and set pending_approval so drain_display_queue
        knows the upcoming done is ask_user-caused and should continue instead of
        emitting task/done."""
        ga = None
        with self._pool_lock:
            ctx = self.pool.get(task_id)
            if ctx is not None:
                ga = ctx.ga
                ctx.pending_approval = True
                ctx._last_approval_input_ref = (task_id, tool_id)
        if self._ask_user_patched and ga is not None:
            self._bridge_ask_user._current_ga = ga
        # The actual approval/request is emitted by the patched ask_user when
        # do_ask_user calls it (next in dispatch, same agent thread) — avoids a
        # race where we emit before ev.wait() is armed.

    def handle_approval_response(self, msg):
        task_id = msg.get("task_id")
        tool_id = msg.get("tool_id")
        slot = self._pending_approvals.pop((task_id, tool_id), None)
        if slot is None:
            self.send_error("stale_approval",
                            f"no pending approval for {tool_id} on {task_id}",
                            original_id=msg.get("id"))
            return
        ev, box = slot
        box["decision"] = msg.get("decision", "reject")
        box["input"] = msg.get("input", "")
        # stash input on ctx so drain_display_queue can re-feed as continuation
        with self._pool_lock:
            ctx = self.pool.get(task_id)
        if ctx is not None:
            ctx._approval_input = box.get("input", "")
        ev.set()  # wake the blocked agent thread
        self.send({"id": msg["id"], "type": "approval/ack", "version": VERSION,
                   "task_id": task_id, "tool_id": tool_id, "status": "accepted"})
```

更新 `drain_display_queue`（Task 2）——在 `if "done" in item:` 块内、构造 `done_msg` **之前**插入 approval 续跑分支：

```python
            if "done" in item:
                # Approval continuation: this done was caused by ask_user
                # (do_ask_user returns should_exit=True → agent_loop break → done).
                # Re-feed the client's approved input as a new prompt on the SAME
                # GA instance (history preserved, task_id unchanged, client sees
                # no task boundary).
                if getattr(ctx, "pending_approval", False):
                    ctx.pending_approval = False
                    cont = getattr(ctx, "_approval_input", None)
                    ctx._approval_input = None
                    if cont is not None and str(cont).strip() != "":
                        ctx.dq = ctx.ga.put_task(cont, source="stdio")
                        continue   # drain the new dq
                    # reject / empty input → fall through to normal done below
                # (normal done path below — unchanged from Task 2)
                done_text = item.get("done", "")
                ...
```

在 `serve()` 的 `self.register_hooks()` **后**追加 `self._patch_ask_user()`：

```python
    def serve(self):
        self.register_hooks()
        self._patch_ask_user()
        for line in self.stdin:
            ...
```

在 `dispatch` 的 business 分支里（`task/interrupt` 后）加：

```python
        elif mtype == "approval/response":
            self.handle_approval_response(msg)
```

- [ ] **Step 4: 跑单元测试确认通过**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: PASS（16 个测试全过）。

- [ ] **Step 5: 勾选 + 提交**

勾选 tasks.md §3.7。

```bash
git add ga_stdio.py tests/test_ga_stdio_unit.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "feat(ga_stdio): approval loop — patch ask_user, block agent thread, continuation on same GA (S4)"
```

---

### Task 6: per-task GA 池 + 有界并发（S2）

**覆盖：** Design Doc §10.6 / §6.2 / tasks.md §3.4 / S2 / Q8。

**Files:**
- Modify: `ga_stdio.py`（`handle_task_start` 升级：semaphore + 排队 + agent_info 填实值）
- Modify: `tests/test_ga_stdio_unit.py`（并发上限 + 排队单元测试）

**Interfaces:**
- Consumes: `threading.Semaphore`、`GenericAgent.list_llms`（agent_info.llm_count）、`MCPClientManager.get_instance`（agent_info.mcp_connected）。
- Produces: `BridgeCore.semaphore`、`BridgeCore._queued`（FIFO）、`handle_task_start` 的 `status:"queued"` 路径。

- [ ] **Step 1: 写失败测试——超并发上限排队**

追加到 `tests/test_ga_stdio_unit.py`：

```python
def test_task_start_over_limit_returns_queued():
    """max_concurrency=2; 3rd task_start → task/ack{status:queued}."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"), max_concurrency=2)
    core._spawn_ga = lambda: _FakeGAWithAbort()  # avoid real GA spawn in unit test
    acks = []
    core.send = lambda m: acks.append(m)
    for i in range(3):
        core.handle_task_start({"id": i, "type": "task/start",
                                "prompt": f"p{i}", "mode": "single"})
    statuses = [a.get("status") for a in acks if a.get("type") == "task/ack"]
    assert statuses[:2] == ["running", "running"]
    assert statuses[2] == "queued"
    assert len(core._queued) == 1


def test_release_wakes_queued_task():
    """When a running task releases, the next queued task starts (running)."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"), max_concurrency=1)
    ga = _FakeGAWithAbort()
    core._spawn_ga = lambda: ga
    acks = []
    core.send = lambda m: acks.append(m)
    core.handle_task_start({"id": 1, "type": "task/start", "prompt": "p1"})
    core.handle_task_start({"id": 2, "type": "task/start", "prompt": "p2"})
    # second is queued
    assert acks[-1]["status"] == "queued"
    # simulate first task done → release
    first_ctx = list(core.pool.values())[0]
    core._release_task(first_ctx)
    import time as _t; _t.sleep(0.05)  # let woken thread ack
    running = [a for a in acks if a.get("status") == "running"]
    assert len(running) >= 2
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: FAIL — `BridgeCore.__init__` 不接受 `max_concurrency`；无 `_queued`。

- [ ] **Step 3: 实现有界并发池**

改 `BridgeCore.__init__` 签名与体（加 `max_concurrency` 参数 + semaphore + _queued；保留 Task 2/3/5 已加的字段）：

```python
    def __init__(self, stdin=None, stdout=None, max_concurrency=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._write_lock = threading.Lock()
        self._next_event_id = 1
        self.initialized = False
        if max_concurrency is None:
            max_concurrency = int(os.environ.get("GA_STDIO_MAX_CONCURRENCY", "4"))
        self.max_concurrency = max_concurrency
        self.semaphore = threading.Semaphore(max_concurrency)
        self.pool = {}
        self._queued = []             # FIFO of (ctx, prompt, images, original_id)
        self._task_counter = 0
        self._pool_lock = threading.Lock()
        self.ga_to_task = {}
        self._tool_id_counter = 0
        self._hooks_registered = False
        self._pending_approvals = {}
        self._ask_user_patched = False
```

重写 `handle_task_start`（替换 Task 2 版本；保留 Task 2 的 `_objective` 设置语义，移到 `_start_task_thread`）：

```python
    def handle_task_start(self, msg):
        prompt = msg.get("prompt", "")
        mode = msg.get("mode", "single")
        budget = msg.get("budget")
        images = msg.get("images", [])
        task_id = self._new_task_id()
        if mode == "autonomous" and not self._validate_budget(budget):
            self.send_error("bad_budget",
                            "autonomous requires budget{seconds? and/or turns?}",
                            original_id=msg.get("id"))
            return
        ctx = TaskCtx(ga=None, dq=None, task_id=task_id, thread=None,
                      mode=mode, budget=budget)
        ctx._objective = prompt
        with self._pool_lock:
            self.pool[task_id] = ctx
        if self.semaphore.acquire(blocking=False):
            self.send({"id": msg["id"], "type": "task/ack", "version": VERSION,
                       "task_id": task_id, "status": "running"})
            self._start_task_thread(ctx, prompt, images)
        else:
            with self._pool_lock:
                self._queued.append((ctx, prompt, images, msg["id"]))
            self.send({"id": msg["id"], "type": "task/ack", "version": VERSION,
                       "task_id": task_id, "status": "queued"})

    def _validate_budget(self, budget):
        if not isinstance(budget, dict):
            return False
        seconds = budget.get("seconds")
        turns = budget.get("turns")
        if seconds is None and turns is None:
            return False
        if seconds is not None and not isinstance(seconds, (int, float)):
            return False
        if turns is not None and not isinstance(turns, int):
            return False
        return True

    def _start_task_thread(self, ctx, prompt, images):
        ga = self._spawn_ga()
        ctx.ga = ga
        with self._pool_lock:
            self.ga_to_task[id(ga)] = ctx.task_id
        dq = ga.put_task(prompt, source="stdio", images=images)
        ctx.dq = dq
        t = threading.Thread(target=self._run_task, args=(ctx,), daemon=True)
        ctx.thread = t
        t.start()
```

更新 `_release_task`（Task 2）——在 `finally` 之外、释放 semaphore 后唤醒一个 queued：

```python
    def _release_task(self, ctx):
        with self._pool_lock:
            self.pool.pop(ctx.task_id, None)
            if ctx.ga is not None:
                self.ga_to_task.pop(id(ctx.ga), None)
        try:
            ctx.ga.shutdown()
        except Exception:
            pass
        self.semaphore.release()
        # wake one queued task if any
        next_item = None
        with self._pool_lock:
            if self._queued:
                next_item = self._queued.pop(0)
        if next_item is not None:
            nxt_ctx, nxt_prompt, nxt_images, _orig_id = next_item
            self._start_task_thread(nxt_ctx, nxt_prompt, nxt_images)
            self.send({"id": self._next_id(), "type": "task/ack", "version": VERSION,
                       "task_id": nxt_ctx.task_id, "status": "running"})
```

更新 `handle_initialize`（Task 1）的 `agent_info` 填实值——替换 Task 1 的 stub `agent_info`：

```python
    def handle_initialize(self, msg):
        caps = msg.get("capabilities", [])
        missing = [c for c in caps if c not in SERVER_CAPABILITIES]
        if missing:
            self.send_error("capability_unsupported",
                            f"server lacks: {missing}", original_id=msg.get("id"))
            return
        self.initialized = True
        llm_count = 0
        try:
            from agentmain import GenericAgent as _GA
            probe = _GA()
            llm_count = len(probe.list_llms())
            probe.shutdown()
        except Exception:
            llm_count = 0
        mcp_connected = 0
        try:
            from mcp_client import MCPClientManager
            mgr = MCPClientManager.get_instance()
            if mgr is not None:
                mcp_connected = len(mgr.registry.get_server_names())
        except Exception:
            mcp_connected = 0
        self.send({"id": msg["id"], "type": "ready", "version": VERSION,
                   "capabilities": SERVER_CAPABILITIES,
                   "agent_info": {"name": "GenericAgent",
                                  "mcp_connected": mcp_connected,
                                  "llm_count": llm_count}})
```

- [ ] **Step 4: 跑单元测试确认通过**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: PASS（18 个测试全过）。

- [ ] **Step 5: 勾选 + 提交**

勾选 tasks.md §3.4。

```bash
git add ga_stdio.py tests/test_ga_stdio_unit.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "feat(ga_stdio): per-task GA pool + bounded concurrency (max 4, env-config) (S2/Q8)"
```

---

### Task 7: autonomous 自续循环 + budget（S7）

**覆盖：** Design Doc §10.7 / §6.5 / tasks.md §3.6 / S7 / Q5。

**Files:**
- Modify: `ga_stdio.py`（`run_autonomous` + CONTINUATION/BUDGET prompt 模板）
- Modify: `tests/test_ga_stdio_unit.py`（budget 校验 + 自续循环用 fake ga）

**Interfaces:**
- Consumes: `reflect/goal_mode.py` 的 `CONTINUATION_PROMPT` 模式（复刻，不 import）、`time.time()`、`GenericAgent.put_task`。
- Produces: `BridgeCore.run_autonomous(ctx)`、`ga_stdio.CONTINUATION_PROMPT`、`ga_stdio.BUDGET_LIMIT_PROMPT`。

- [ ] **Step 1: 写失败测试——budget 校验 + 自续循环**

追加到 `tests/test_ga_stdio_unit.py`：

```python
def test_validate_budget_requires_at_least_one_field():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    assert core._validate_budget({"seconds": 2}) is True
    assert core._validate_budget({"turns": 1}) is True
    assert core._validate_budget({"seconds": 2, "turns": 3}) is True
    assert core._validate_budget({}) is False
    assert core._validate_budget(None) is False
    assert core._validate_budget("oops") is False


def test_run_autonomous_emits_budget_done_when_seconds_exhausted():
    """With budget{seconds:0} the first continuation is immediately the wrap-up
    round → task/done{reason:budget}."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)

    class _GA:
        def __init__(self):
            self.calls = 0
        def put_task(self, prompt, source=None, images=None):
            dq = queue.Queue()
            if self.calls == 0:
                dq.put({"next": "work", "source": source, "turn": 1, "outputs": ["work"]})
                dq.put({"done": "work done", "source": source, "turn": 1, "outputs": ["work done"]})
            else:
                dq.put({"done": "[wrap up]", "source": source, "turn": 2, "outputs": ["[wrap]"]})
            self.calls += 1
            return dq
        def abort(self): pass
        def shutdown(self): pass

    ctx = ga_stdio.TaskCtx(ga=_GA(), dq=None, task_id="tA", thread=None,
                           mode="autonomous", budget={"seconds": 0})
    import time as _t
    ctx.start_time = _t.time() - 1   # force elapsed >= seconds immediately
    with core._pool_lock:
        core.pool["tA"] = ctx
        core.ga_to_task[id(ctx.ga)] = "tA"
    core.run_autonomous(ctx)
    reasons = [m for m in sent if m.get("type") == "task/done"]
    assert reasons and any(r["reason"] == "budget" for r in reasons)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: FAIL — `run_autonomous` 仍 raise NotImplementedError（Task 2 占位）；`_validate_budget` 在 Task 6 已实现，本步 `run_autonomous` 是关键。

- [ ] **Step 3: 实现 autonomous 自续循环**

在 `ga_stdio.py` 模块级（`SERVER_CAPABILITIES` 后）加 prompt 模板（复刻 `reflect/goal_mode.py:26-69` 模式，不从 reflect import）：

```python
CONTINUATION_PROMPT = """[Autonomous — 持续优化]

<objective>
{objective}
</objective>

⏱ 已用 {elapsed_min:.0f} 分钟，剩余约 {remaining_min:.0f} 分钟。第 {turn} 次唤醒。

你正处在 autonomous 模式下工作：无法宣告完成，你会被持续唤醒直到预算耗尽。
唤醒后流程（3选1）：
1. 创造阶段(第一次唤醒)：分析 objective，在 cwd 建工作文件夹，严格按 objective 执行
2. 检验阶段：换视角检验产出，从读者/用户/测试工程师角度找问题
3. 改进阶段：针对检验报告实质改进交付物

原则：每次唤醒交替检验与改进；除非严重问题不全部重写；交付物不混入"已检验"等中间信息。
"""

BUDGET_LIMIT_PROMPT = """[Autonomous — 预算耗尽，收口]

<objective>
{objective}
</objective>

⏱ 预算已耗尽。这是最后一轮。请执行收口：
1. 总结本次所有进展
2. 列出未完成事项和 next step
3. 确保工作文件夹记录关键成果
"""
```

实现 `run_autonomous`（替换 Task 2 的 NotImplementedError 占位）：

```python
    def run_autonomous(self, ctx):
        """Bridge-owned autonomous continuation loop (D5: no reflect import).

        Re-feeds CONTINUATION_PROMPT until budget exhausted, then BUDGET_LIMIT_PROMPT
        for wrap-up, then task/done{reason:budget}. task/interrupt → reason:interrupted.
        The first put_task (original objective) was done by _start_task_thread; we
        drain it as iteration 0, then loop.
        """
        budget = ctx.budget or {}
        seconds = budget.get("seconds")
        turns = budget.get("turns")
        objective = ctx._objective or "continue the task"
        # drain the first (original) iteration
        self._drain_one_task_iteration(ctx)
        if ctx.interrupted:
            self._emit_done(ctx, "interrupted"); return
        while True:
            if ctx.interrupted:
                self._emit_done(ctx, "interrupted"); return
            elapsed = time.time() - ctx.start_time
            ctx.turns_used += 1
            seconds_exhausted = (seconds is not None and elapsed >= seconds)
            turns_exhausted = (turns is not None and ctx.turns_used > turns)
            if seconds_exhausted or turns_exhausted:
                prompt = BUDGET_LIMIT_PROMPT.format(objective=objective)
                ctx.dq = ctx.ga.put_task(prompt, source="stdio")
                self._drain_one_task_iteration(ctx)
                self._emit_done(ctx, "budget"); return
            remaining = (seconds - elapsed) if seconds is not None else float("inf")
            prompt = CONTINUATION_PROMPT.format(
                objective=objective,
                elapsed_min=elapsed / 60,
                remaining_min=remaining / 60,
                turn=ctx.turns_used)
            ctx.dq = ctx.ga.put_task(prompt, source="stdio")
            self._drain_one_task_iteration(ctx)
            if ctx.interrupted:
                self._emit_done(ctx, "interrupted"); return

    def _drain_one_task_iteration(self, ctx):
        """Drain display_queue for one put_task iteration (until done)."""
        while True:
            try:
                item = ctx.dq.get(timeout=2200)
            except queue.Empty:
                self._emit_done(ctx, "error", error="display_queue timeout")
                return
            if "next" in item:
                self.send({"id": self._next_id(), "type": "task/delta", "version": VERSION,
                           "task_id": ctx.task_id, "content": item.get("next", ""),
                           "turn": item.get("turn", 0)})
                continue
            if "done" in item:
                return   # one iteration complete; caller decides next

    def _emit_done(self, ctx, reason, error=None):
        done = {"id": self._next_id(), "type": "task/done", "version": VERSION,
                "task_id": ctx.task_id, "reason": reason,
                "turn": getattr(ctx, "turns_used", 0)}
        if error:
            done["error"] = error
        self.send(done)
```

- [ ] **Step 4: 跑单元测试确认通过**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: PASS（20 个测试全过）。

- [ ] **Step 5: 勾选 + 提交**

勾选 tasks.md §3.6。

```bash
git add ga_stdio.py tests/test_ga_stdio_unit.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "feat(ga_stdio): autonomous continuation loop + budget{seconds,turns} (S7/Q5)"
```

---

### Task 8: slash/cmd 转发（S8/S9）

**覆盖：** Design Doc §10.8 / §6.6 / tasks.md §3.10 / S8/S9 / Q6。

**Files:**
- Modify: `ga_stdio.py`（`handle_slash_cmd` + 注入/状态/raw 三路径）
- Modify: `tests/test_ga_stdio_unit.py`（slash 路由单元测试）

**Interfaces:**
- Consumes: `frontends.slash_cmds.prompt_for(cmd, args_text)`（`frontends/slash_cmds.py:597-614`）、`GenericAgent.put_task`（raw 转发 `/llm`/`/session.*`/`/resume`）、`TaskCtx`。
- Produces: `BridgeCore.handle_slash_cmd`。

- [ ] **Step 1: 写失败测试——slash 三路径**

追加到 `tests/test_ga_stdio_unit.py`：

```python
def test_slash_injection_command_routes_via_prompt_for():
    """/goal <target> → prompt_for returns injected prompt → new task on a fresh GA."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    core._spawn_ga = lambda: _FakeGAWithAbort()
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_slash_cmd({"id": 1, "type": "slash/cmd",
                           "cmd": "/goal", "args": "build a web"})
    acks = [m for m in sent if m.get("type") == "task/ack"]
    results = [m for m in sent if m.get("type") == "slash/result"]
    assert len(acks) == 1
    assert len(results) == 1
    assert results[0].get("injected_prompt")
    assert results[0].get("task_id") == acks[0]["task_id"]


def test_slash_state_command_routes_raw():
    """/llm 2 → raw put_task on a fresh GA (state-class, _handle_slash_cmd internal)."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    captured = {}
    class _GA(_FakeGAWithAbort):
        def put_task(self, q, source=None, images=None):
            captured["raw"] = q
            dq = queue.Queue(); dq.put({"done": "ok", "source": source, "turn": 0, "outputs": []}); return dq
    core._spawn_ga = lambda: _GA()
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_slash_cmd({"id": 1, "type": "slash/cmd", "cmd": "/llm", "args": "2"})
    assert captured["raw"] == "/llm 2"
    results = [m for m in sent if m.get("type") == "slash/result"]
    assert len(results) == 1


def test_slash_unsupported_command_emits_error():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_slash_cmd({"id": 1, "type": "slash/cmd", "cmd": "/scheduler", "args": ""})
    assert sent[-1]["type"] == "error"
    assert sent[-1]["code"] == "slash_unsupported"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: FAIL — `BridgeCore` 无 `handle_slash_cmd`。

- [ ] **Step 3: 实现 slash 转发**

在 `ga_stdio.py` 顶部加注入命令集合（与 `frontends/slash_cmds.py:605-612` 的 table 一致）：

```python
_INJECTION_SLASH_CMDS = {"/update", "/autorun", "/morphling", "/goal", "/hive", "/conductor"}
_STATE_SLASH_CMDS = {"/llm", "/resume"}  # /session.* handled by prefix match
```

在 `BridgeCore` 内加方法：

```python
    def handle_slash_cmd(self, msg):
        cmd = (msg.get("cmd") or "").strip()
        args = msg.get("args", "") or ""
        if not cmd.startswith("/"):
            self.send_error("slash_unsupported", f"not a slash command: {cmd!r}",
                            original_id=msg.get("id"))
            return
        # /scheduler is out of protocol scope (touches local FS, no LLM)
        if cmd == "/scheduler":
            self.send_error("slash_unsupported",
                            "/scheduler is not part of the v1 protocol",
                            original_id=msg.get("id"))
            return
        from frontends.slash_cmds import prompt_for
        injected = None
        if cmd in _INJECTION_SLASH_CMDS:
            injected = prompt_for(cmd, args)
        if injected is not None:
            # injection-class: run injected prompt as a new task
            task_id = self._new_task_id()
            ctx = TaskCtx(ga=None, dq=None, task_id=task_id, thread=None,
                          mode="single", budget=None)
            ctx._objective = injected
            with self._pool_lock:
                self.pool[task_id] = ctx
            if self.semaphore.acquire(blocking=False):
                self._start_task_thread(ctx, injected, [])
                self.send({"id": msg["id"], "type": "slash/result", "version": VERSION,
                           "task_id": task_id,
                           "injected_prompt": injected[:200]})
            else:
                with self._pool_lock:
                    self._queued.append((ctx, injected, [], msg["id"]))
                self.send({"id": msg["id"], "type": "slash/result", "version": VERSION,
                           "task_id": task_id, "injected_prompt": injected[:200]})
            return
        # state-class: raw forward to _handle_slash_cmd via put_task (agentmain.py:155-186)
        if cmd in _STATE_SLASH_CMDS or cmd.startswith("/session."):
            raw = f"{cmd} {args}".strip()
            task_id = self._new_task_id()
            ctx = TaskCtx(ga=None, dq=None, task_id=task_id, thread=None,
                          mode="single", budget=None)
            ctx._objective = raw
            with self._pool_lock:
                self.pool[task_id] = ctx
            if self.semaphore.acquire(blocking=False):
                self._start_task_thread(ctx, raw, [])
            else:
                with self._pool_lock:
                    self._queued.append((ctx, raw, [], msg["id"]))
            self.send({"id": msg["id"], "type": "slash/result", "version": VERSION,
                       "task_id": task_id})
            return
        self.send_error("slash_unsupported", f"unknown slash command: {cmd!r}",
                        original_id=msg.get("id"))
```

在 `dispatch` 的 business 分支里（`approval/response` 后）加：

```python
        elif mtype == "slash/cmd":
            self.handle_slash_cmd(msg)
```

- [ ] **Step 4: 跑单元测试确认通过**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: PASS（23 个测试全过）。

- [ ] **Step 5: 勾选 + 提交**

勾选 tasks.md §3.10。

```bash
git add ga_stdio.py tests/test_ga_stdio_unit.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "feat(ga_stdio): slash/cmd forward — injection via prompt_for + raw state-class (S8/S9/Q6)"
```

---

### Task 9: llm/list + llm/select + session/resume（S6）

**覆盖：** Design Doc §10.9 / tasks.md §3.9 / S6。

**Files:**
- Modify: `ga_stdio.py`（`handle_llm_list` / `handle_llm_select` / `handle_session_resume`）
- Modify: `tests/test_ga_stdio_unit.py`（llm/session 单元测试用 fake ga）

**Interfaces:**
- Consumes: `GenericAgent.list_llms()` / `next_llm(n)` / `llmclient.backend.history`（`agentmain.py:117-135`）。
- Produces: `BridgeCore.handle_llm_list` / `handle_llm_select` / `handle_session_resume`。

- [ ] **Step 1: 写失败测试——llm list/select + session resume**

追加到 `tests/test_ga_stdio_unit.py`：

```python
class _FakeGAWithLLM:
    def __init__(self):
        self.switched_to = None
        self.history_set = None
    def list_llms(self):
        return [(0, "openai/gpt-4", True), (1, "anthropic/claude", False)]
    def next_llm(self, n=-1):
        self.switched_to = n
    def shutdown(self): pass


def _make_ctx_with_ga(core, ga, task_id="tL"):
    ctx = ga_stdio.TaskCtx(ga=ga, dq=queue.Queue(), task_id=task_id, thread=None)
    with core._pool_lock:
        core.pool[task_id] = ctx
        core.ga_to_task[id(ga)] = task_id
    return ctx


def test_llm_list_emits_models_with_current():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithLLM()
    _make_ctx_with_ga(core, ga, "tL")
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_llm_list({"id": 1, "type": "llm/list", "task_id": "tL"})
    m = [x for x in sent if x["type"] == "llm/list"][0]
    assert m["current_no"] == 0
    assert {"no": 0, "name": "openai/gpt-4", "current": True} in m["llms"]
    assert {"no": 1, "name": "anthropic/claude", "current": False} in m["llms"]


def test_llm_select_switches_model():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithLLM()
    _make_ctx_with_ga(core, ga, "tL")
    core.handle_llm_select({"id": 1, "type": "llm/select", "task_id": "tL", "n": 1})
    assert ga.switched_to == 1


def test_session_resume_restores_history():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    class _GA(_FakeGAWithLLM):
        def __init__(self):
            super().__init__()
            class _Backend: history = None
            self.llmclient = type("C", (), {"backend": _Backend()})()
    ga = _GA()
    _make_ctx_with_ga(core, ga, "tS")
    core.handle_session_resume({"id": 1, "type": "session/resume",
                                "task_id": "tS",
                                "history": [{"role": "user", "content": "hi"}],
                                "llm_no": 0})
    assert ga.llmclient.backend.history == [{"role": "user", "content": "hi"}]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: FAIL — `BridgeCore` 无 `handle_llm_list` 等。

- [ ] **Step 3: 实现 llm/session 桥接**

在 `BridgeCore` 内加方法（llm/list/select 和 session/resume 操作一个**已存在** task 的 GA；v1 要求带 task_id，作用域于该 task 的 GA）：

```python
    def _ctx_for(self, task_id, msg):
        with self._pool_lock:
            ctx = self.pool.get(task_id)
        if ctx is None or ctx.ga is None:
            self.send_error("unknown_task", f"no GA for task {task_id!r}",
                            original_id=msg.get("id"))
            return None
        return ctx

    def handle_llm_list(self, msg):
        ctx = self._ctx_for(msg.get("task_id"), msg)
        if ctx is None:
            return
        rows = ctx.ga.list_llms()  # [(i, name, current_bool)]
        llms = [{"no": i, "name": n, "current": c} for i, n, c in rows]
        current_no = next((i for i, _, c in rows if c), 0)
        self.send({"id": msg["id"], "type": "llm/list", "version": VERSION,
                   "llms": llms, "current_no": current_no})

    def handle_llm_select(self, msg):
        ctx = self._ctx_for(msg.get("task_id"), msg)
        if ctx is None:
            return
        n = msg.get("n", 0)
        try:
            ctx.ga.next_llm(n)
            self.send({"id": msg["id"], "type": "llm/ack", "version": VERSION,
                       "task_id": ctx.task_id, "selected_no": n, "ok": True})
        except Exception as e:
            self.send_error("llm_select_failed", f"{type(e).__name__}: {e}",
                            original_id=msg.get("id"))

    def handle_session_resume(self, msg):
        ctx = self._ctx_for(msg.get("task_id"), msg)
        if ctx is None:
            return
        history = msg.get("history", [])
        llm_no = msg.get("llm_no")
        try:
            ctx.ga.llmclient.backend.history = history
            if llm_no is not None:
                ctx.ga.next_llm(llm_no)
            self.send({"id": msg["id"], "type": "session/ack", "version": VERSION,
                       "task_id": ctx.task_id, "ok": True})
        except Exception as e:
            self.send_error("session_resume_failed", f"{type(e).__name__}: {e}",
                            original_id=msg.get("id"))
```

在 `dispatch` 的 business 分支里（`slash/cmd` 后）加：

```python
        elif mtype == "llm/list":
            self.handle_llm_list(msg)
        elif mtype == "llm/select":
            self.handle_llm_select(msg)
        elif mtype == "session/resume":
            self.handle_session_resume(msg)
```

- [ ] **Step 4: 跑单元测试确认通过**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: PASS（26 个测试全过）。

- [ ] **Step 5: 勾选 + 提交**

勾选 tasks.md §3.9。

```bash
git add ga_stdio.py tests/test_ga_stdio_unit.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "feat(ga_stdio): llm/list + llm/select + session/resume bridging (S6)"
```

---

### Task 10: mcp/list（S5）

**覆盖：** Design Doc §10.10 / tasks.md §3.8 / S5。

**Files:**
- Modify: `ga_stdio.py`（`handle_mcp_list`）
- Modify: `tests/test_ga_stdio_unit.py`（mcp/list 单元测试用 fake registry）

**Interfaces:**
- Consumes: `MCPClientManager.get_instance()` + `registry.get_server_names()` + `registry._tools`（`mcp_client.py:203,217,235,582-604`）。
- Produces: `BridgeCore.handle_mcp_list`。

- [ ] **Step 1: 写失败测试——mcp/list 结构化输出**

追加到 `tests/test_ga_stdio_unit.py`：

```python
def test_mcp_list_emits_structured_servers(monkeypatch):
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)

    class _FakeRegistry:
        _tools = {
            "github": [{"name": "create_issue", "description": "open an issue",
                        "inputSchema": {"type": "object"}}],
            "slack":  [{"name": "post", "description": "post msg",
                        "inputSchema": {"type": "object"}}],
        }
        def get_server_names(self):
            return list(self._tools.keys())

    class _FakeMgr:
        registry = _FakeRegistry()

    import mcp_client as _mc
    monkeypatch.setattr(_mc.MCPClientManager, "get_instance",
                        classmethod(lambda cls: _FakeMgr()))
    core.handle_mcp_list({"id": 1, "type": "mcp/list"})
    m = [x for x in sent if x["type"] == "mcp/list"][0]
    names = [s["name"] for s in m["servers"]]
    assert "github" in names and "slack" in names
    gh = next(s for s in m["servers"] if s["name"] == "github")
    assert gh["tools"][0]["name"] == "create_issue"


def test_mcp_list_no_manager_emits_empty_servers():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)
    # MCPClientManager.get_instance() returns None when no GA has been spawned
    core.handle_mcp_list({"id": 1, "type": "mcp/list"})
    m = [x for x in sent if x["type"] == "mcp/list"][0]
    assert m["servers"] == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: FAIL — `BridgeCore` 无 `handle_mcp_list`。

- [ ] **Step 3: 实现 mcp/list**

在 `BridgeCore` 内加方法：

```python
    def handle_mcp_list(self, msg):
        servers = []
        try:
            from mcp_client import MCPClientManager
            mgr = MCPClientManager.get_instance()
            if mgr is not None:
                registry = mgr.registry
                for name in registry.get_server_names():
                    tools = registry._tools.get(name, [])  # list[{name,description,inputSchema}]
                    servers.append({
                        "name": name,
                        "tools": [{"name": t.get("name", ""),
                                   "description": t.get("description", "")}
                                  for t in tools],
                    })
        except Exception as e:
            self.send_error("mcp_list_failed", f"{type(e).__name__}: {e}",
                            original_id=msg.get("id"))
            return
        self.send({"id": msg["id"], "type": "mcp/list", "version": VERSION,
                   "servers": servers})
```

在 `dispatch` 的 business 分支里（`session/resume` 后）加：

```python
        elif mtype == "mcp/list":
            self.handle_mcp_list(msg)
```

- [ ] **Step 4: 跑单元测试确认通过 + 全套回归**

```bash
cd D:/GenericAgent && python -m pytest tests/test_ga_stdio_unit.py -v
```

Expected: PASS（28 个测试全过）。同时跑全套确认无回归：

```bash
cd D:/GenericAgent && python -m pytest -x -q
```

Expected: 现有 199 pytest + 新 28 单元测试全绿，无回归。

- [ ] **Step 5: 勾选 + 提交**

勾选 tasks.md §3.8。

```bash
git add ga_stdio.py tests/test_ga_stdio_unit.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "feat(ga_stdio): mcp/list structured visibility via MCPClientManager registry (S5)"
```

---

### Task 11: 契约测试 11 个逐个补齐（黑盒 wire 测试，断结构非内容）

**覆盖：** tasks.md §4.1-4.11 / Design Doc §8 / Q7。

**Files:**
- Create: `tests/_protocol_helpers.py`（spawn + 发/收 JSON 行 harness）
- Create: `tests/test_protocol_transport.py`（§4.1）
- Create: `tests/test_protocol_capability.py`（§4.2）
- Create: `tests/test_protocol_single_task.py`（§4.3）
- Create: `tests/test_protocol_multi_session.py`（§4.4）
- Create: `tests/test_protocol_interrupt.py`（§4.5）
- Create: `tests/test_protocol_approval.py`（§4.6）
- Create: `tests/test_protocol_mcp_visibility.py`（§4.7）
- Create: `tests/test_protocol_llm_session.py`（§4.8）
- Create: `tests/test_protocol_autonomous.py`（§4.9）
- Create: `tests/test_protocol_slash_forward.py`（§4.10）
- Create: `tests/test_protocol_resilience.py`（§4.11）

**Interfaces:**
- Consumes: `ga_stdio.BridgeCore`（Task 1-10 完整实现）、`subprocess.Popen`、`threading.Thread`（reader）。
- Produces: 11 个黑盒 wire 测试 + 共享 harness。

**测试原则（全部 11 个文件共同遵守）：**
- **黑盒**：`subprocess.Popen([sys.executable, "-m", "ga_stdio"])`，stdin 发 JSON 行，stdout 读 JSON 行。
- **断结构非内容**：断言消息 `type` 序列、必填字段存在、终态 `reason` 正确；**不断言** LLM 输出文本内容（非确定）。
- **超时**：每个 readline 用超时（reader 线程 + join）——LLM 响应慢，但协议结构要快。
- **环境**：测试需要至少一个可用 LLM（`mykey.py` 配置）。CI 无 LLM 时跳过——用 `needs_llm` 守卫；纯协议结构测试（§4.1/§4.2/§4.7/§4.11 E3/E2）不需要 LLM，不守卫。

- [ ] **Step 1: 写共享 harness**

`tests/_protocol_helpers.py`：

```python
"""Black-box wire-test harness for ga_stdio protocol tests.

Spawns `python -m ga_stdio` as a child; sends JSON lines on stdin; reads
JSON lines from stdout with a timeout. Asserts on message structure
(type sequence / required fields / terminal reason), never on LLM text.
"""
import json
import os
import subprocess
import sys
import threading
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_HAS_LLM = bool(os.environ.get("GA_HAS_LLM") or os.environ.get("MYKEY_PATH"))
try:
    import pytest
    needs_llm = pytest.mark.skipif(not _HAS_LLM,
                                   reason="no LLM configured (set GA_HAS_LLM=1)")
except Exception:
    def needs_llm(fn): return fn


class BridgeProc:
    def __init__(self, timeout=10.0):
        env = dict(os.environ)
        env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "ga_stdio"],
            cwd=_REPO_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, text=True, bufsize=1)
        self._deadline = time.monotonic() + timeout

    def send(self, msg):
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def recv(self, timeout=None):
        """Read one JSON line from stdout. Returns parsed dict or None on timeout/EOF."""
        if timeout is None:
            timeout = max(0.0, self._deadline - time.monotonic())
        if timeout <= 0:
            return None
        box = []
        t = threading.Thread(target=lambda: box.append(self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive() or not box or box[0] == "":
            return None
        try:
            return json.loads(box[0])
        except (json.JSONDecodeError, ValueError):
            return None

    def recv_until(self, predicate, timeout=30.0, max_msgs=200):
        """Read messages until predicate(msg) is True or timeout. Returns collected list."""
        out = []
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(out) < max_msgs:
            m = self.recv(timeout=min(5.0, max(0.1, end - time.monotonic())))
            if m is None:
                continue
            out.append(m)
            if predicate(m):
                break
        return out

    def close(self):
        try: self.proc.stdin.close()
        except Exception: pass
        try: self.proc.wait(timeout=5)
        except Exception:
            try: self.proc.kill()
            except Exception: pass

    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def initialize(bp, caps=None):
    """Perform initialize/ready handshake; returns ready message."""
    bp.send({"id": 1, "type": "initialize", "version": "1",
             "capabilities": caps or ["streaming", "multi-session", "autonomous",
                                       "approval", "mcp", "slash", "llm-switch",
                                       "session-resume"]})
    msg = bp.recv(timeout=10)
    assert msg is not None, "no ready after initialize"
    assert msg["type"] == "ready"
    assert msg["version"] == "1"
    return msg
```

- [ ] **Step 2: 写 §4.1 transport + E2**

`tests/test_protocol_transport.py`：

```python
"""§4.1 transport: initialize/ready handshake + malformed JSON doesn't crash (E2)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _protocol_helpers import BridgeProc, initialize


def test_handshake_returns_ready_with_caps_and_agent_info():
    with BridgeProc() as bp:
        ready = initialize(bp)
        assert "capabilities" in ready and len(ready["capabilities"]) > 0
        ai = ready["agent_info"]
        assert ai["name"] == "GenericAgent"
        assert "mcp_connected" in ai and "llm_count" in ai


def test_malformed_json_emits_bad_json_error_and_keeps_running():
    """E2: a bad line → error{code:bad_json}; next valid line still processed."""
    with BridgeProc() as bp:
        initialize(bp)
        bp.proc.stdin.write("this is not json\n")
        bp.proc.stdin.flush()
        err = bp.recv(timeout=5)
        assert err is not None and err["type"] == "error"
        assert err["code"] == "bad_json"
        # bridge must still be alive — send mcp/list, expect a response
        bp.send({"id": 99, "type": "mcp/list", "version": "1"})
        m = bp.recv(timeout=5)
        assert m is not None and m["type"] == "mcp/list"
```

- [ ] **Step 3: 写 §4.2 capability**

`tests/test_protocol_capability.py`：

```python
"""§4.2 capability negotiation: unsupported cap → graceful error+exit."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _protocol_helpers import BridgeProc


def test_unsupported_capability_emits_capability_unsupported():
    with BridgeProc() as bp:
        bp.send({"id": 1, "type": "initialize", "version": "1",
                 "capabilities": ["streaming", "nonexistent-cap"]})
        msg = bp.recv(timeout=5)
        assert msg["type"] == "error"
        assert msg["code"] == "capability_unsupported"
        assert "nonexistent-cap" in msg["message"]
```

- [ ] **Step 4: 写 §4.3 single_task（S1）**

`tests/test_protocol_single_task.py`：

```python
"""§4.3 single-task end-to-end streaming (S1). Asserts structure, not content."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_single_task_emits_ack_delta_then_done_completed():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Reply with the single word: ok", "mode": "single"})
        ack = bp.recv(timeout=5)
        assert ack["type"] == "task/ack"
        assert ack["status"] in ("running", "queued")
        task_id = ack["task_id"]
        collected = bp.recv_until(lambda m: m.get("type") == "task/done" and m.get("task_id") == task_id,
                                  timeout=120)
        dones = [m for m in collected if m["type"] == "task/done"]
        assert dones, "expected task/done"
        assert dones[-1]["reason"] in ("completed", "error")
        assert dones[-1]["task_id"] == task_id
        deltas = [m for m in collected if m["type"] == "task/delta" and m.get("task_id") == task_id]
        assert len(deltas) >= 1
```

- [ ] **Step 5: 写 §4.4 multi_session（S2）**

`tests/test_protocol_multi_session.py`：

```python
"""§4.4 multi-session: two concurrent tasks, task_ids not crossed (S2)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_two_concurrent_tasks_have_distinct_ids_and_no_cross_talk():
    with BridgeProc(timeout=150) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Reply: A", "mode": "single"})
        bp.send({"id": 11, "type": "task/start", "version": "1",
                 "prompt": "Reply: B", "mode": "single"})
        ack1 = bp.recv(timeout=5); ack2 = bp.recv(timeout=5)
        assert ack1["type"] == "task/ack" and ack2["type"] == "task/ack"
        tids = {ack1["task_id"], ack2["task_id"]}
        assert len(tids) == 2  # distinct
        # collect until both done
        seen = set()
        collected = bp.recv_until(lambda m: m.get("type") == "task/done" and (seen.add(m["task_id"]) or len(seen) == 2),
                                  timeout=120, max_msgs=500)
        done_tids = {m["task_id"] for m in collected if m.get("type") == "task/done"}
        assert done_tids == tids  # both done, no leakage to other id
        delta_tids = {m["task_id"] for m in collected if m.get("type") == "task/delta"}
        assert delta_tids.issubset(tids)
```

- [ ] **Step 6: 写 §4.5 interrupt（S3）**

`tests/test_protocol_interrupt.py`：

```python
"""§4.5 interrupt a running task → task/done{reason:interrupted} (S3)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_interrupt_running_task_yields_interrupted_done():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Write a very long essay about the history of computing.",
                 "mode": "single"})
        ack = bp.recv(timeout=5)
        task_id = ack["task_id"]
        # wait until running (first delta), then interrupt
        bp.recv_until(lambda m: m.get("type") == "task/delta", timeout=30)
        bp.send({"id": 11, "type": "task/interrupt", "version": "1", "task_id": task_id})
        collected = bp.recv_until(lambda m: m.get("type") == "task/done" and m.get("task_id") == task_id,
                                  timeout=30)
        dones = [m for m in collected if m["type"] == "task/done"]
        assert dones
        assert dones[-1]["reason"] == "interrupted"
```

- [ ] **Step 7: 写 §4.6 approval（S4）**

`tests/test_protocol_approval.py`：

```python
"""§4.6 approval closed loop (S4). Agent asks_user → approval/request → response → continue."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_approval_request_then_response_continues_task():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Call ask_user to ask me whether to proceed, then reply 'done'.",
                 "mode": "single"})
        ack = bp.recv(timeout=5); task_id = ack["task_id"]
        collected = bp.recv_until(lambda m: m.get("type") == "approval/request", timeout=90)
        reqs = [m for m in collected if m["type"] == "approval/request"]
        if not reqs:
            pytest.skip("agent did not call ask_user in this run (LLM nondeterminism)")
        req = reqs[-1]
        assert req["task_id"] == task_id
        assert req["prompt"]
        bp.send({"id": 20, "type": "approval/response", "version": "1",
                 "task_id": task_id, "tool_id": req["tool_id"],
                 "decision": "approve", "input": "yes, proceed"})
        done = bp.recv_until(lambda m: m.get("type") == "task/done" and m.get("task_id") == task_id,
                             timeout=120)
        dones = [m for m in done if m["type"] == "task/done"]
        assert dones
        assert dones[-1]["reason"] in ("completed", "error")
```

- [ ] **Step 8: 写 §4.7 mcp_visibility（S5）**

`tests/test_protocol_mcp_visibility.py`：

```python
"""§4.7 mcp/list visibility query (S5). Structure only, no MCP config required."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _protocol_helpers import BridgeProc, initialize


def test_mcp_list_returns_servers_array_structure():
    with BridgeProc() as bp:
        initialize(bp)
        bp.send({"id": 50, "type": "mcp/list", "version": "1"})
        msg = bp.recv(timeout=10)
        assert msg["type"] == "mcp/list"
        assert isinstance(msg["servers"], list)
        for s in msg["servers"]:
            assert "name" in s and "tools" in s
            for t in s["tools"]:
                assert "name" in t
```

- [ ] **Step 9: 写 §4.8 llm_session（S6）**

`tests/test_protocol_llm_session.py`：

```python
"""§4.8 llm/list + llm/select + session/resume (S6)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_llm_list_and_select_then_session_resume():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Reply: hello", "mode": "single"})
        ack = bp.recv(timeout=5); task_id = ack["task_id"]
        bp.recv_until(lambda m: m.get("type") == "task/done", timeout=120)
        bp.send({"id": 20, "type": "llm/list", "version": "1", "task_id": task_id})
        m = bp.recv(timeout=10)
        assert m["type"] == "llm/list"
        assert isinstance(m["llms"], list) and len(m["llms"]) >= 1
        assert "current_no" in m
        if len(m["llms"]) > 1:
            target = (m["current_no"] + 1) % len(m["llms"])
            bp.send({"id": 21, "type": "llm/select", "version": "1",
                     "task_id": task_id, "n": target})
            ack2 = bp.recv(timeout=10)
            assert ack2["type"] == "llm/ack"
        bp.send({"id": 22, "type": "session/resume", "version": "1",
                 "task_id": task_id, "history": [], "llm_no": 0})
        s = bp.recv(timeout=10)
        assert s["type"] == "session/ack"
```

- [ ] **Step 10: 写 §4.9 autonomous（S7）**

`tests/test_protocol_autonomous.py`：

```python
"""§4.9 autonomous: run to budget exhaustion + interrupt (S7)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_autonomous_budget_exhaustion_yields_budget_done():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Summarize what 1+1 is.", "mode": "autonomous",
                 "budget": {"seconds": 2}})
        ack = bp.recv(timeout=5); task_id = ack["task_id"]
        collected = bp.recv_until(lambda m: m.get("type") == "task/done" and m.get("task_id") == task_id,
                                  timeout=120)
        dones = [m for m in collected if m["type"] == "task/done"]
        assert dones
        assert dones[-1]["reason"] in ("budget", "interrupted", "completed", "error")


@needs_llm
def test_autonomous_interrupt_yields_interrupted_done():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Write an essay about AI.", "mode": "autonomous",
                 "budget": {"turns": 99}})
        ack = bp.recv(timeout=5); task_id = ack["task_id"]
        bp.recv_until(lambda m: m.get("type") == "task/delta", timeout=60)
        bp.send({"id": 11, "type": "task/interrupt", "version": "1", "task_id": task_id})
        collected = bp.recv_until(lambda m: m.get("type") == "task/done" and m.get("task_id") == task_id,
                                  timeout=30)
        dones = [m for m in collected if m["type"] == "task/done"]
        assert dones
        assert dones[-1]["reason"] == "interrupted"
```

- [ ] **Step 11: 写 §4.10 slash_forward（S8/S9）**

`tests/test_protocol_slash_forward.py`：

```python
"""§4.10 slash/cmd forward: injection-class + state-class (S8/S9)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _protocol_helpers import BridgeProc, initialize, needs_llm


def test_scheduler_returns_slash_unsupported():
    with BridgeProc() as bp:
        initialize(bp)
        bp.send({"id": 1, "type": "slash/cmd", "version": "1",
                 "cmd": "/scheduler", "args": ""})
        m = bp.recv(timeout=5)
        assert m["type"] == "error" and m["code"] == "slash_unsupported"


@needs_llm
def test_goal_injection_returns_slash_result_with_task_id():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 1, "type": "slash/cmd", "version": "1",
                 "cmd": "/goal", "args": "say hello"})
        m = bp.recv(timeout=5)
        assert m["type"] == "slash/result"
        assert m.get("task_id")
        assert m.get("injected_prompt")
        done = bp.recv_until(lambda x: x.get("type") == "task/done" and x.get("task_id") == m["task_id"],
                             timeout=120)
        assert any(x["type"] == "task/done" for x in done)
```

- [ ] **Step 12: 写 §4.11 resilience（E1 + E3）**

`tests/test_protocol_resilience.py`：

```python
"""§4.11 resilience: child crash (E1) + uninitialized business msg rejected (E3)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _protocol_helpers import BridgeProc, initialize


def test_uninitialized_task_start_emits_not_initialized():
    """E3: task/start before initialize → error{code:not_initialized}."""
    with BridgeProc() as bp:
        bp.send({"id": 1, "type": "task/start", "version": "1", "prompt": "hi"})
        m = bp.recv(timeout=5)
        assert m["type"] == "error"
        assert m["code"] == "not_initialized"


def test_child_crash_surfaces_as_stdout_eof_no_hang():
    """E1: kill the child; client sees stdout EOF (recv returns None), not a hang."""
    with BridgeProc() as bp:
        initialize(bp)
        bp.proc.kill()
        bp.proc.wait(timeout=5)
        m = bp.recv(timeout=3)
        assert m is None
```

- [ ] **Step 13: 跑全部 wire 测试**

```bash
cd D:/GenericAgent && python -m pytest tests/test_protocol_*.py -v
```

Expected: 不需要 LLM 的（§4.1 transport / §4.2 capability / §4.7 mcp / §4.11 resilience / §4.10 scheduler）PASS；需要 LLM 的（§4.3-4.6, §4.8-4.10 含 goal 注入）在有 LLM 环境时 PASS，无 LLM 时 skip。

- [ ] **Step 14: 全套回归**

```bash
cd D:/GenericAgent && python -m pytest -x -q
```

Expected: 现有 199 + 新单元 28 + 新 wire 测试（按 LLM 可用性）全绿，无回归。

- [ ] **Step 15: 勾选 + 提交**

勾选 tasks.md §4.1-4.11 全部。

```bash
git add tests/_protocol_helpers.py tests/test_protocol_transport.py tests/test_protocol_capability.py tests/test_protocol_single_task.py tests/test_protocol_multi_session.py tests/test_protocol_interrupt.py tests/test_protocol_approval.py tests/test_protocol_mcp_visibility.py tests/test_protocol_llm_session.py tests/test_protocol_autonomous.py tests/test_protocol_slash_forward.py tests/test_protocol_resilience.py openspec/changes/add-durable-agent-protocol/tasks.md
git commit -m "test(ga_stdio): 11 black-box wire tests — transport/capability/single/multi/interrupt/approval/mcp/llm/autonomous/slash/resilience (§4.1-4.11)"
```

---

## Self-Review（计划作者自查）

**1. Spec coverage**（Design Doc §5 消息 schema + S1-S9/E1-E3 + §10 实现顺序 → tasks.md §3/§4 全覆盖）：

| Spec 项 | 实现 task | 契约测试 task |
|---|---|---|
| §3 传输/帧 + E2 畸形 JSON | T1 | T11 §4.1 |
| §4 能力协商 + 不匹配 | T1 | T11 §4.2 |
| E3 未初始化 | T1 | T11 §4.11 |
| §5 `initialize`/`ready` | T1（ready agent_info 实值 T6） | T11 §4.1 |
| §5 `task/start`/`task/ack` | T2 + T6（并发/排队） | T11 §4.3/§4.4 |
| §5 `task/delta`/`task/done` + reason 枚举 | T2 | T11 §4.3 |
| §5 `tool/call`（tool_before） | T3 | T11 §4.3（隐含 delta 间夹 tool/call） |
| §5 `tool/result`（turn_after） | T3 | T11 §4.3 |
| §5 `task/interrupt` → interrupted | T4 | T11 §4.5 |
| §5 `approval/request`/`response` + 线程模型 | T5 | T11 §4.6 |
| §5 `llm/list`/`llm/select`/`session/resume` | T9 | T11 §4.8 |
| §5 `slash/cmd`/`slash/result` | T8 | T11 §4.10 |
| §5 `mcp/list` | T10 | T11 §4.7 |
| §5 `error`（bad_json/not_initialized/unknown_type/capability_unsupported/stale_approval/child_crashed/slash_unsupported/bad_budget/unknown_task） | T1/T3/T5/T6/T8/T9/T10 散布 | T11 §4.1/§4.2/§4.11/§4.10 |
| S1 单 task 流式 | T2 | T11 §4.3 |
| S2 多会话并发 | T6 | T11 §4.4 |
| S3 中断 | T4 | T11 §4.5 |
| S4 approval | T5 | T11 §4.6 |
| S5 MCP 可见性 | T10 | T11 §4.7 |
| S6 llm/session | T9 | T11 §4.8 |
| S7 autonomous + budget + 中断 | T7 | T11 §4.9 |
| S8/S9 slash 转发 | T8 | T11 §4.10 |
| E1 child 崩溃 | T1（serve EOF 自然退出） | T11 §4.11 |
| §10 实现顺序 1-11 | T1-T11 一一对应 | — |
| Q1-Q8 决策 | T1(version/caps)/T2+T6(Q4 落点+Q8 池)/T3(Q3 hook)/T7(Q5 budget)/T8(Q6 slash)/T11(Q7 测试) | — |

无 spec 遗漏。

**2. Placeholder scan**：计划内每步都有可执行代码或可运行命令。无 "TBD/TODO/implement later"。Task 5 的 drain 续跑分支、Task 6 的 `_queued` 唤醒、Task 9 的 llm/list list comp 均给出了**清晰版**可直接写——实现者照写，不要照抄示意段。

**3. Type consistency**：`TaskCtx` 字段（`ga/dq/task_id/thread/mode/budget/start_time/turns_used/interrupted/pending_approval/_last_approval_input_ref/_approval_input/_objective`）在 T2 定义、T4/T5/T6/T7/T8 增量引用，名称一致。`BridgeCore` 方法跨 task 引用名一致：`handle_initialize/handle_task_start/handle_task_interrupt/handle_approval_response/handle_slash_cmd/handle_llm_list/handle_llm_select/handle_session_resume/handle_mcp_list/drain_display_queue/run_autonomous/_spawn_ga/_release_task/_start_task_thread/_run_task/_drain_one_task_iteration/_emit_done/_on_turn_after/_emit_tool_call/_on_approval_request/register_hooks/_patch_ask_user/send/send_error/_next_id/_new_task_id/_ctx_for/_validate_budget`。`_resolve_ga_from_ctx` 在 T3 定义、T5 通过 `ga_to_task` 反查路径一致。`_pending_approvals` 在 T3 stub、T5 实现，键 `(task_id, tool_id)` 一致。`reason` 枚举（completed/budget/interrupted/error）在 T2/T4/T5/T7 一致。`VERSION="1"`、`SERVER_CAPABILITIES` 在 T1 定义、T6 引用，一致。

**计划就绪。**

## Execution Handoff

计划已保存至 `docs/superpowers/plans/2026-07-19-durable-agent-protocol.md`。两种执行方式：

**1. Subagent-Driven（推荐）** —— 每 task 派发独立 subagent，task 间 review，快速迭代。REQUIRED SUB-SKILL: superpowers:subagent-driven-development。

**2. Inline Execution** —— 在当前会话用 `executing-plans` 批量执行，带 checkpoint review。REQUIRED SUB-SKILL: superpowers:executing-plans。

**注意：** Task 0a 是阻塞型前置——必须先推进 `add-selfextract-installer` 出 open 阶段，否则 Task 1+ 的源码写会被 comet hook 硬拦。Task 0b 是无冲突确认，秒过。















