## 1. Rust workspace 与项目骨架

- [x] 1.1 新建 Rust workspace（单二进制 crate `ga`，模块拆分 core/cli/runner/ipc/store）
- [x] 1.2 添加依赖（tokio、rusqlite、serde、clap、tracing、portable-pty 等）并 `cargo build` 通过
- [x] 1.3 建立日志/错误类型骨架与配置加载（GA 根目录、数据目录定位）

## 2. SQLite 数据层（对应 D3）

- [x] 2.1 设计并实现 schema：sessions / projects / events / approvals / runner_kinds / config
- [x] 2.2 实现 Store（WAL 模式、连接池、事务辅助）与 migration 入口
- [x] 2.3 实现 CRUD 方法（sessions_list/create/archive、projects_list/create/follow、config_get/set）

## 3. IPC 协议层

- [x] 3.1 定义 IpcMessage / EventKind / ApprovalRequest / ApprovalResponse / RiskLevel
- [x] 3.2 平台抽象（Windows Named Pipe / Unix socket）+ 自动重连
- [x] 3.3 事件流写入器（EventStream：JSON 行协议 + 滚动文件）

## 4. Runner trait + GA adapter

- [x] 4.1 定义 Runner trait（kind/start/stop/status）+ RunnerContext/Output/Status
- [x] 4.2 实现 GaRunner（portable-pty 启动 GA agent_loop，PID 捕获）
- [x] 4.3 实现 RunnerRegistry（内置 GA + OpenCode + Claude-Code，配置文件声明自定义）

## 5. Core 编排层

- [x] 5.1 Orchestrator 持有 Store + RunnerRegistry，统一入口
- [x] 5.2 session_new：创建记录 → 启动 runner → 回写 PID
- [x] 5.3 status / sessions_list / projects_list / llm_set 等查询命令

## 6. CLI 命令（clap）

- [x] 6.1 `ga status`：活跃会话 / 项目 / runner / LLM 模型
- [x] 6.2 `ga session new/list/archive/watch`
- [x] 6.3 `ga project create/list/follow`
- [x] 6.4 `ga llm set` / `ga daemon`

## 7. Daemon 模式 + 事件流消费

- [ ] 7.1 Daemon 前台/后台模式（当前为 stub）
- [ ] 7.2 事件流消费：监听各 session 的 IPC，写入 SQLite events
- [ ] 7.3 跨重启 reattach：扫描 sessions 表，对 active session 尝试 runner.status→reattach

## 8. 审批系统

- [ ] 8.1 Approval schema + store 方法（create/pending_list/update）
- [ ] 8.2 风险分级策略（Low→auto, Medium→prompt, High→block）
- [ ] 8.3 runner 调用前经 Core 策略检查、需审批则挂起
- [ ] 8.4 实现 CLI `approval <id> --approve/--reject`（带 origin 三元组）与交互提示
- [ ] 8.5 验证 allowlist 命中 / YOLO 自动批准写入事件流

## 9. 集成验证与跨平台

- [ ] 9.1 端到端：CLI 派发 session（GA + 非 GA 各一）→ 事件流 → 跨重启 reattach
- [ ] 9.2 端到端：风险工具/命令审批闭环（结构化 + 流两种，CLI 形态）
- [ ] 9.3 Linux / macOS / Windows 三平台构建与冒烟
- [ ] 9.4 更新顶层 README / 启动脚本（`ga` / `ga.cmd` 指向新二进制）
