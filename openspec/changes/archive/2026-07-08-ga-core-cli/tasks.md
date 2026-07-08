## 1. Rust workspace 与项目骨架

- [ ] 1.1 新建 Rust workspace（单二进制 crate `ga`，模块拆分 core/cli/runner/ipc/store）
- [ ] 1.2 添加依赖（tokio、rusqlite、serde、clap、tracing、portable-pty 等）并 `cargo build` 通过
- [ ] 1.3 建立日志/错误类型骨架与配置加载（GA 根目录、数据目录定位）

## 2. SQLite 数据层（对应 D3）

- [ ] 2.1 设计并实现 schema：sessions / projects / events / approvals / runner_kinds（WAL + 迁移）
- [ ] 2.2 实现 sessions/projects 的 CRUD 仓储层 + 单测
- [ ] 2.3 实现 events 时间线写入与按 session/project 查询 + 单测

## 3. Core daemon 基础（对应 D1）

- [ ] 3.1 实现 daemon 入口（单实例锁、按需拉起、信号处理）
- [ ] 3.2 实现本地 IPC 监听端点（Unix socket / Windows named pipe 抽象）
- [ ] 3.3 实现 client↔daemon 连接层（CLI 前端连 Core）

## 4. Runner 抽象与 adapter（对应 D2/D4）

- [ ] 4.1 定义 `Runner` trait + 事件枚举（结构化 tool_call / 流 output+status）+ 版本化 schema
- [ ] 4.2 实现 GA adapter（拉起 Python GA、结构化 tool_call 事件回推、cwd/memory 注入）
- [ ] 4.3 实现 generic-PTY/stream adapter（opencode/claude-code/codex，output 行 + status）
- [ ] 4.4 实现 runner kind 注册/选择（`--runner`，内置 + 配置文件声明）
- [ ] 4.5 用 mock runner 验证 spawn→事件流→终答/完成 闭环（结构化 + 流两种）

## 5. 事件时间线（对应 ga-orchestration-core spec）

- [ ] 5.1 结构化 runner 的 tool_call args/result/timing 经 Core 写入 events
- [ ] 5.2 流 runner 的 output 行 / status 变化经 Core 写入 events
- [ ] 5.3 实现 `session watch` / `project follow` 事件流聚合输出；验证跨重启可重放

## 6. 跨平台进程管理（对应 D5）

- [ ] 6.1 定义 `trait RunnerSupervisor`（spawn / terminate / kill）
- [ ] 6.2 Unix 实现（process group + SIGTERM 优雅停）
- [ ] 6.3 Windows 实现（Job Object 子树随 Core 退出 + ConPTY）
- [ ] 6.4 三平台冒烟：Core 退出后无 orphan runner

## 7. CLI 命令面与契约（对应 supervisor-cli spec / D6）

- [ ] 7.1 用 clap 实现 `status` / `sessions list` / `session new --runner` / `session watch` / `session archive` / `project create` / `project follow` / `llm set`
- [ ] 7.2 实现写命令必填 origin 三元组校验与缺失/未知 runner 报错（退出码 2）
- [ ] 7.3 实现 `--json` 版本化输出（含 `schema_version`）与稳定退出码（0/2/3/4/5/10+）
- [ ] 7.4 旧 `ga_cli` 兼容 shim 决策与实现（保留透传子集或移除）

## 8. 审批治理（两级，对应 approval-governance spec / D7）

- [ ] 8.1 结构化 runner 工具级策略（per-call / allowlist / YOLO）
- [ ] 8.2 流 runner 命令级确认（pre-run confirm / YOLO）
- [ ] 8.3 runner 调用前经 Core 策略检查、需审批则挂起
- [ ] 8.4 实现 CLI `approval <id> --approve/--reject`（带 origin 三元组）与交互提示
- [ ] 8.5 验证 allowlist 命中 / YOLO 自动批准写入事件流

## 9. 集成验证与跨平台

- [ ] 9.1 端到端：CLI 派发 session（GA + 非 GA 各一）→ 事件流 → 跨重启 reattach
- [ ] 9.2 端到端：风险工具/命令审批闭环（结构化 + 流两种，CLI 形态）
- [ ] 9.3 Linux / macOS / Windows 三平台构建与冒烟
- [ ] 9.4 更新顶层 README / 启动脚本（`ga` / `ga.cmd` 指向新二进制）
