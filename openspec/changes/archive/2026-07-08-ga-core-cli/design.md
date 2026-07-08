## Context

GenericAgent 是 Python 自进化 agent 框架（agentmain/agent_loop/llmcore/ga 四模块 + L0-L4 memory + 9 原子工具 + self-evolving）。当前前端：`ga_cli`（Python 子进程分发器，仅启停前端进程）、`tuiapp_v2`（单会话 Textual TUI）、`qtapp`（PyQt5 GUI）、IM bots。无多会话编排层、无给 Supervisor Agent 的稳定 CLI 契约、无工具时间线/审批，且不能 multiplex GA 之外的 agent（opencode / claude-code / codex）。

Galley（GenericAgent 衍生品）证明 GUI/CLI 对等前端共连 Rust Core 的模型；herdr 证明单 Rust 二进制 agent-agnostic multiplexer（detach/reattach、跨重启、多 agent）。本设计取 herdr 的 runner 抽象 + Galley 的编排语义。

约束：不重写任何 agent 引擎（作为外部 runner 接入）；跨平台（Linux/macOS/Windows，当前环境 WSL2/Windows）；单二进制无 Electron；v1 不含 socket API / 插件。

## Goals / Non-Goals

**Goals:**
- agent-agnostic Core（`Runner` trait + adapter 注册表）拥有 session/project 状态 + SQLite + 通用流事件时间线 + 跨重启 + 跨平台
- 稳定 Supervisor CLI 契约（命令面含 `--runner`、origin 三元组、JSON schema、退出码）
- 审批 / allowlist / YOLO（结构化 runner 工具级 + 流 runner 命令级）

**Non-Goals:**
- TUI multiplexer（Item 3）
- Goal 控制器（Item 2）
- socket API / 插件
- 重写任何 agent 引擎 / IM 前端
- 内嵌 CPython（runner 用系统/打包的运行时）

## Decisions

### D1: 单二进制 = Core daemon + CLI 前端（同进程多角色）
Rust 单二进制 `ga`：daemon 模式运行时是 Core（owning 状态 + runner）；CLI 子命令运行时是前端客户端，经本地 IPC 与已运行 daemon 通信，未运行则按需拉起。
- 为何：对齐 herdr「one binary」与 Galley「peer frontends over Core」；避免多进程部署复杂度。
- 替代：分离 daemon + cli 两二进制 —— 部署/路径管理更复杂，否决。

### D2: Runner 抽象 = 可插拔 `Runner` trait + adapter 注册表（agent-agnostic）
Core 不直接耦合任何 agent。定义 `trait Runner`：`spawn(cfg) -> Stream<Event>`、`send(input)`、`terminate()`。adapter：GA adapter（发射结构化 tool_call args/result/timing/turn_end）、generic-PTY adapter（opencode/claude-code/codex，发射 output 行 + status blocked/working/done）。`session new --runner=<kind>` 选择 adapter。
- 为何：B 方案，真 herdr 任意 agent；GA 深度特性经 adapter 注入而不污染 Core。
- 替代：GA-bound（A，锁死 GA，否决）；混合（C，Core 仍需 Runner trait，与 B 本质同，但 C 把 GA 特权化，用户选 B 不特权化，采纳 B）。
- Open Question：runner kind 发现/注册机制（内置 vs 配置文件声明）。

### D3: 状态存储 = SQLite（WAL 模式）
sessions / projects / events / approvals / runner_kinds 入 SQLite，WAL 提升并发读写、跨重启一致。GA 的 memory/skills 留在 runner 侧文件（GA adapter 只读写）。
- 为何：Galley 同款；可搜索历史；单文件易备份。
- 替代：纯 JSON 文件 —— 并发写/搜索差，否决。

### D4: 多 agent 语义 = 多并发 Runner 实例（可异构），Core 管理
"多面板/多 agent" = Core 按需 spawn 多个 Runner（可异构：一个 GA + 一个 codex），各自独立 cwd/env；subagent 预算（Item 2）= Core 限制可并存 Runner 数。Core 不在进程内跑 agent 逻辑。
- 为何：崩溃隔离 + 异构 agent 共存（herdr 核心）。

### D5: 跨平台进程管理 = 抽象层
定义 `trait RunnerSupervisor`（spawn / terminate / kill）。Unix: process group + SIGTERM 优雅停；Windows: Job Object 确保子树随 Core 退出。
- 为何：Windows 上 orphan runner 是真实风险；Job Object 是唯一可靠手段。

### D6: CLI 契约稳定性 = 命令面 + JSON schema + 退出码冻结
命令面与 JSON 输出 schema 视为公开契约，版本化（`--json` 输出带 `schema_version`）；退出码语义固定（0 ok / 2 usage / 3 not-found / 4 conflict / 5 rate-limit / 10+ 业务）。`session new` 增 `--runner=<kind>`（默认 `ga`）。origin 三元组必填于所有写操作。
- 为何：Supervisor Agent 依赖稳定契约编程；对齐 Galley。

### D7: 审批治理 = Core 策略引擎，两级
结构化 runner（GA）：工具调用前经 Core 策略检查（per-call / allowlist / YOLO），需审批则挂起经 IPC 请求决策。流 runner（generic-PTY）：命令/pane 级确认（运行前 confirm）+ YOLO；无工具级审批（因无结构化工具事件）。
- 为何：agent-agnostic 下不能假设所有 runner 都有结构化工具事件；分级保证流 runner 也有治理。
- Open Question：流 runner 的"风险命令"分类策略（正则/关键词 vs 无）。

## Risks / Trade-offs

- [Runner trait 抽象不当致 adapter 难写] → 先写 GA + generic-PTY 两个 adapter 验证 trait 覆盖度
- [PTY 跨平台差异（Windows ConPTY）] → 抽象 Pty trait + 三平台 CI 冒烟
- [流 runner 无结构化事件，时间线/审批弱] → 明确流 runner 仅 output+status，审批降为命令级
- [runner kind 发现机制] → 内置常用 + 配置文件声明自定义
- [GA runner adapter 启动慢/依赖] → GA 运行时定位策略设计阶段定（系统 Python / .venv / bundled CPython）

## Open Questions

- runner kind 发现/注册机制（内置 vs 配置声明）
- 流 runner 风险命令分类策略
- GA 运行时定位（系统 / .venv / bundled CPython）
- 旧 `ga_cli` 兼容 shim 保留范围
- IM 前端 v1 是否接入 Core
