## ADDED Requirements

### Requirement: Task 工具扇出隔离子 agent

主循环 agent SHALL 能调用 `task` 工具,把一个有界工作扇出给运行在独立 git worktree 中的子 agent。`task` 工具 SHALL 同时接受单个任务(返回单个结果对象)与任务列表(返回结果对象列表)。

#### Scenario: 单任务扇出
- **WHEN** 主循环 agent 调用 `task(description=..., result_schema=...)` 传入单个任务
- **THEN** 系统创建一个独立 worktree、在其中运行子 agent、返回一个经 `result_schema` 校验的结果对象给父 agent 直读

#### Scenario: 批模式扇出
- **WHEN** 主循环 agent 调用 `task` 传入 N 个任务的列表
- **THEN** 系统创建 N 个独立 worktree 并并行运行 N 个子 agent、返回长度为 N 的结果对象列表(顺序与输入对应)

### Requirement: 子 agent 返回 schema-校验的结构化结果

子 agent MUST 以调用注入的 `submit_result` 工具作为终末动作,其参数即返回对象;工具侧 MUST 按 caller 传入的 `result_schema` 校验,校验不过则报错让子 agent 重试。父 agent SHALL 直读校验通过的对象,不得解析散文。

#### Scenario: 合规结果
- **WHEN** 子 agent 调用 `submit_result` 且参数符合 `result_schema`
- **THEN** 父 agent 收到该对象本身(JSON 值),无需任何散文解析

#### Scenario: 非合规结果触发重试
- **WHEN** 子 agent 调用 `submit_result` 但参数不符合 `result_schema`
- **THEN** 工具返回校验错误、子 agent 被要求重试;若最终未产出合规对象,结果标记 `failed` 且 worktree 保留

### Requirement: 每 worker 独立 git worktree 隔离

每个 worker SHALL 运行在从 `base_ref`(默认当前 HEAD)创建的专属 git worktree 中,兄弟 worker 不得共享工作目录。worktree SHALL 创建在 `.ga/worktrees/<uuid>/` 下,且 `.ga/` 在 `.gitignore` 中。

#### Scenario: 兄弟隔离
- **WHEN** 两个 worker 各自在其 worktree 中编辑同名文件
- **THEN** 两者互不干扰、不产生冲突或孤儿编辑

### Requirement: 批并发受 max_concurrent 限

当 `task` 传入列表时,manager SHALL 以 `ThreadPoolExecutor` 并行运行 worker,并发上限为 `max_concurrent`(默认 4,env `GA_SUBAGENT_MAX_CONCURRENT` 可调);超出上限的 worker SHALL 排队等待,不被拒绝。

#### Scenario: 超限排队
- **WHEN** 列表长度 N > `max_concurrent`
- **THEN** 同时运行数不超过 `max_concurrent`,其余排队,最终全部完成并返回 N 个结果

### Requirement: 超时杀进程组并保留 worktree

worker SHALL 在独立进程组中 spawn(POSIX `start_new_session` / Windows `CREATE_NEW_PROCESS_GROUP`)。`timeout_s` 到期时 manager SHALL 杀掉整个进程组(含 agentmain 派生的 bash/git 子进程)、标记结果 `timed_out`、保留 worktree 供排查。

#### Scenario: 超时触发
- **WHEN** worker 运行达到 `timeout_s` 仍未完成
- **THEN** 整个进程组被杀、结果标记 `timed_out`、对应 worktree 不被清理

### Requirement: 失败保留与启动 GC

failed / timed_out 的 worktree SHALL 保留至 registry `retained[]`(上限 `max_retained`,默认 16,超限删最旧)。parent 启动时 SHALL 扫描 `.ga/worktrees/*`,凡无活进程占用的孤儿 worktree 予以 `git worktree prune` + 删目录。

#### Scenario: 失败保留
- **WHEN** 某 worker failed 或 timed_out
- **THEN** 其 worktree 路径记入 `retained[]` 供排查,不自动删除

#### Scenario: 启动清理孤儿
- **WHEN** parent 启动且 `.ga/worktrees/` 下存在无活进程占用的孤儿 worktree
- **THEN** 这些孤儿被 prune 并删目录,不残留

### Requirement: legacy 不破 + feature gate

conductor / ultraplan / btw 的行为 SHALL NOT 改变。`task` 能力 SHALL 由 feature gate `GA_SUBAGENT_ENABLED` 控制,默认 off;gate 关闭时 `task` 工具不暴露给主循环 agent。

#### Scenario: gate 关闭
- **WHEN** `GA_SUBAGENT_ENABLED` 未设或为 0
- **THEN** 主循环 agent 工具表面不含 `task`,legacy 行为完全不变

#### Scenario: legacy 不受影响
- **WHEN** `task` 能力启用后调用 conductor / ultraplan / btw
- **THEN** 三者行为与本变更前一致
