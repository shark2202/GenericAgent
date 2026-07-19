## Context

GenericAgent（GA）当前 16+ 前端全是 Python，进程内 `from agentmain import GenericAgent` 直接调用 SDK。Rust 等非 Python 前端无法 import Python 类，需一个跨语言契约面。现有 `--task`/`--func` 文件协议（`agentmain.py:301-330`）是无版本号、无 schema、无兼容承诺的实现偶然；GA 的 `assets/ga_httpapp.py`（HTTP 轮询）和 `frontends/conductor.py`（WS 编排）是两个现有跨进程桥，但前者非流式、后者 schema 过重且编排中心。5 年视角下，需要新造一个为耐久设计的协议。

## Goals / Non-Goals

**Goals:**
- 冻结 5 年耐久 agent 协议 v1，作为非 Python 前端驱动 GA 的稳定契约面。
- 覆盖 GA 招牌能力：单任务流式 + autonomous（goal/hive/autonomous）自续 + 多会话并发 + approval + MCP 可见性 + slash 转发。
- 引擎内部（agent_loop / ga.py / llmcore / hermes）可自由演化，只要不破协议契约。
- 交付参考实现（Python stdio bridge）+ 契约测试，补现有 199 pytest 的 wire/CLI 协议盲区。

**Non-Goals:**
- 不做 Rust CLI/TUI/GUI（→ `add-rust-frontends`）。
- 不改 `agent_loop.py` / `ga.py` / `llmcore.py` 引擎内部。
- 不移除 `--task`/`--func`（coexist）。
- 不暴露 reflect 脚本控制到协议（留 Python 内部）。
- 不在 design.md 定确切消息 schema / 版本化字段（→ Design Doc，design 阶段 brainstorming）。

## Decisions

**D1 — Rust = UI 层，Python = 稳定引擎。**
Rationale: Python SDK（`agentmain.GenericAgent`）刚验证端到端跑通（199 pytest green），是稳定核心；Rust 只做 UI。
Alternatives: 复活废弃的 `ga_rust_cli`/`ga_rust_tui`（Rust 核心移植，9 编译错误半途废弃）——重蹈覆辙，否决。

**D2 — 新造协议，不复用 `--task`/`--func` 文件协议。**
Rationale: 文件协议（`input.txt` / `output*.txt` / `reply.txt` / `_stop`）是实现偶然，无契约级保障；5 年里 GA 循环演化（多轮 / streaming / interrupt）极可能被改。新造耐久协议，带版本号 + capability 协商。
Alternatives: A4 直接锁文件协议加版本号——锁死低保障面，否决。

**D3 — 传输 = 子进程 + stdio JSON-RPC（每行一 JSON，带 `id`/`type`/`version`）。**
Rationale: 进程边界清晰、双向流式、不占端口、跨平台、CI 易测、LSP / Claude Code 同一成熟模型。1 Rust UI ↔ 1 Python child。
Alternatives: HTTP+WS 服务（`ga_httpapp` / `conductor` 底子）——占端口、生命周期管理重、鉴权，适合远程但本 change 非目标；两都要（stdio + WS）——实现量最大，留未来。

**D4 — v1 能力范围 = 最大。**
含：`initialize`/`ready` 协商 · `task/start`（含 `mode:autonomous` + `budget`）· `task/delta` · `tool/call` · `tool/result` · `task/done`（`reason`）· `task/interrupt` · `llm/list` · `llm/select` · `session/resume` · 多会话并发（`task_id`）· approval 回路 · MCP 可见性 · slash 转发。
Rationale: GA 招牌能力（goal/hive/autonomous）必须在 v1，否则协议 Missing the point of GA。5 年耐久 + 最大面需配版本化 + capability 协商（→ Design Doc）防冻结成枷锁。

**D5 — goal/hive/autonomous = slash 转发 + autonomous 生命周期，非专门模式消息。**
Rationale: GA 实际架构里这些模式 = prompt 注入（`slash_cmds.py`）+ reflect 循环持续唤醒（`goal_mode.py`），不是独立引擎调用。协议加两块：slash 命令转发（bridge 注入对应 prompt）+ autonomous task 生命周期（`task/start{mode,budget}` → 持续 delta → `task/done{reason:budget|interrupt}`）。reflect 脚本控制不暴露。
Alternatives: 专门 `goal/start` `hive/start` 消息——最显式但冻结面最大，否决。

**D6 — `--task`/`--func` coexist 不动。**
Rationale: 非目标不改引擎；新协议是 Rust / 非 Python 客户端的稳定面，`--task`/`--func` 留 Python 内部用。未来是否 deprecate 看后续。

## Risks / Trade-offs

- [v1 最大面 = 冻结面大，5 年里改任一能力都要升 v2] → 缓解：版本化 + capability 协商（Design Doc 定 semver 严格度 + 协商字段）；前向兼容策略（v2 server 是否仍服务 v1 client）Design Doc 定。
- [stdio 点对点 = 不支持多 Rust UI 连一 Python child] → 接受；远程多客户端需未来 WS 变体（本 change 非目标）。
- [autonomous 生命周期建模未定（budget 计量、自续语义）] → Design Doc brainstorming 定。
- [slash 转发需 bridge 复刻 `slash_cmds.py` 注入逻辑] → Design Doc 定是 `slash/cmd` 消息还是 raw prompt 转发。
- [`add-selfextract-installer` open 阶段阻塞源码写] → build 前推进 / 收尾它。
- [`hermes-isolated-skill-scorer` 在 design 阶段并行] → 不冲突（不同 change，open/design 只写 `openspec/*`）。

## Open Questions（→ Design Doc，design 阶段 brainstorming）

- 版本化策略：semver 严格度、capability negotiation 字段设计。
- 前向兼容：v2 server 是否仍服务 v1 client、deprecation 窗口。
- 确切消息 schema：每 `type` 的 JSON 字段。
- Python stdio bridge 落点：新模块（如 `ga_stdio.py`）vs 扩展 `agentmain --stdio` 模式。
- autonomous 生命周期建模：`mode`/`budget` 字段、budget 计量方式（token? turn? time?）、自续触发语义。
- slash 转发消息形式：`slash/cmd` 消息 vs raw prompt 转发。
- 契约测试范围：每 v1 能力的断言粒度。
