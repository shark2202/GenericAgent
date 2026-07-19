# agent-protocol Specification

## Purpose
TBD - created by archiving change add-durable-agent-protocol. Update Purpose after archive.
## Requirements
### Requirement: stdio JSON-RPC 传输
协议 SHALL 经子进程 stdin/stdout 传输，每行一个 JSON 对象；每条消息 MUST 含 `id`/`type`/`version` 字段。Rust UI MUST 作为 Python child 的父进程启动。

#### Scenario: 握手成功
- **WHEN** Rust child 启动后发送 `initialize` 消息（含 client caps + proto=v1）
- **THEN** Python 回 `ready` 消息（含 agent caps + proto=v1），此后允许业务消息

#### Scenario: 畸形 JSON 不崩
- **WHEN** Rust 发送一行非法 JSON
- **THEN** Python 回一条 error 消息（带原 id），不崩溃、不断链

### Requirement: capability 协商
`initialize`/`ready` SHALL 交换双方能力清单。能力不匹配时 MUST 优雅报错退出，不得崩溃。

#### Scenario: 能力不匹配优雅退出
- **WHEN** client 要求某 v1 能力而 server 不支持
- **THEN** 协商失败，client 收到明确 error 并优雅退出（不崩溃）

### Requirement: 单任务流式生命周期
client MUST 能经 `task/start` 起一个任务，收到流式 `task/delta`、`tool/call`、`tool/result` 事件，以 `task/done` 终结。

#### Scenario: 单任务端到端
- **WHEN** client 发 `task/start{prompt}`
- **THEN** 收到 ≥1 条 `task/delta`（流式内容）+ 零或多条 `tool/call`/`tool/result` + 终态 `task/done`

### Requirement: 多会话并发
`task/start` SHALL 返回 `task_id`；同一 Python child 内多个任务 MUST 能并发流式、互不串台。

#### Scenario: 双任务并发不串台
- **WHEN** client 连续发两个 `task/start`（不同 prompt）
- **THEN** 各自收到带对应 `task_id` 的 `task/delta`/`task/done`，事件不串混

### Requirement: 任务中断
client MUST 能经 `task/interrupt` 中止运行中的任务；agent SHALL 以 `task/done{reason:interrupted}` 终结。

#### Scenario: 中断运行中任务
- **WHEN** client 对运行中 task 发 `task/interrupt`
- **THEN** 该 task 停止生成，收到 `task/done{reason:interrupted}`

### Requirement: approval 人机回路
agent SHALL 能在需要人决策时发 `approval/request`；client MUST 能响应使 agent 继续。

#### Scenario: approval 闭环
- **WHEN** agent 发 `approval/request`
- **THEN** client 响应后 agent 继续该 task 直到 `task/done`

### Requirement: MCP 工具可见性
client MUST 能查询已连接的 MCP server 及其工具清单。

#### Scenario: 查询 MCP 可见性
- **WHEN** client 查询 capabilities/MCP
- **THEN** 返回当前已连接 MCP server 与工具清单（无连接时返回空）

### Requirement: LLM 切换与会话恢复
client MUST 能 `llm/list` 列模型、`llm/select` 切模型、`session/resume` 恢复历史续聊。

#### Scenario: 切模型 + 恢复会话
- **WHEN** client 发 `llm/select{n}` 后发 `session/resume{history}`
- **THEN** agent 切到指定模型并基于历史续聊

### Requirement: autonomous 任务生命周期
client MUST 能经 `task/start{mode:autonomous, budget}` 起一个持续自续的任务；agent SHALL 持续发 `task/delta` 直到 budget 耗尽（`task/done{reason:budget}`）或被 `task/interrupt`（`reason:interrupted`）。

#### Scenario: autonomous 持续到 budget
- **WHEN** client 发 `task/start{mode:autonomous, budget}`
- **THEN** 持续收到 `task/delta`，budget 耗尽后收 `task/done{reason:budget}`

#### Scenario: autonomous 被中断
- **WHEN** autonomous 任务运行中 client 发 `task/interrupt`
- **THEN** 收 `task/done{reason:interrupted}`

### Requirement: slash 命令转发
client MUST 能转发 `/goal` `/hive` `/morphling` `/conductor` `/update` `/autorun` 等 slash 命令；bridge SHALL 注入对应模式 prompt（复刻 `frontends/slash_cmds.py` 逻辑）。

#### Scenario: slash 转发注入正确 prompt
- **WHEN** client 发 `/goal 调研 X 预算 50k`
- **THEN** bridge 注入 goal 模式 prompt（含读 `memory/goal_mode_sop.md` 指引），agent 进入 goal 模式行为

#### Scenario: hive 经 slash 触发多会话编排
- **WHEN** client 发 `/hive <task>`
- **THEN** 触发蜂群编排（queen + workers 多会话）

### Requirement: child 崩溃韧性
Python child 中途崩溃时 client MUST 检测进程退出并产生 error 事件，不得挂死。

#### Scenario: child 崩溃
- **WHEN** child 在 task 运行中进程退出（非正常 done）
- **THEN** client 检测到退出，本地产生 error 事件，不阻塞等待

### Requirement: 初始化强制
未完成 `initialize` 前，业务消息（如 `task/start`）MUST 被拒，回协议错。

#### Scenario: 未初始化直接 task/start
- **WHEN** client 未发 `initialize` 直接发 `task/start`
- **THEN** server 回 protocol error，拒绝执行

