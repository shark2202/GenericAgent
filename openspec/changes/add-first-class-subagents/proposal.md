## Why

GA 现有三种子 agent 机制（`frontends/conductor.py` 进程内线程池、`assets/ga_ultraplan.py` `_subagent` 子进程、`frontends/btw_cmd.py` `/btw` 侧问）无一达到 omp「first-class subagents」标准：兄弟 worker 共享工作目录（易产生孤儿编辑/冲突）、返回散文需父端解析、且无统一 `task` 工具使主循环 agent 自身能扇出。需补一等公民 `task` 工具：每 worker 独立 git worktree、返回 schema-校验的结构化对象（父直读、无散文）、批并发，对齐 omp 同时不破坏 legacy。

## What Changes

- 新增 `task` 工具条目（`assets/tools_schema.json` + `assets/tools_schema_cn.json`，第 13 项）：参数 `description` / `result_schema`（必填）+ `base_ref` / `model` / `tools` / `timeout_s`（可选）；既收单任务也收任务列表（批模式）。
- 新增 `ga.py` `do_task()`（gen-style，仿 `do_skill_manage`）：解析参数 → 调 `subagent_manager.run_isolated(...)` → 把 schema-校验结果对象 yield 回主循环。
- 新增 `plugins/subagent_manager.py`（`SubagentManager` 单例）：worktree 建/删（`.ga/worktrees/<uuid>`，`.ga/` 加 gitignore）、subprocess spawn（独立进程组）、批并发（`ThreadPoolExecutor`，`max_concurrent` 默认 4 / env `GA_SUBAGENT_MAX_CONCURRENT`）、超时 kill、失败保留 worktree、启动 GC 孤儿 worktree。
- 新增子模式 schema 强制：子 agent 注入 `submit_result` 工具，必须以调用它为终末动作；工具侧按 `result_schema` 校验，不过则报错重试；`agentmain.py` 子模式（`GA_TASK_MODE=isolated` + `GA_TASK_RESULT_SCHEMA=<json>`）装载该工具并约束 tool 子集。
- 抽共享 `assets/subprocess_worker.py`（从 ultraplan `_subagent` / `parallel` 模式提炼）：subprocess + worktree + 结果文件 + 超时 + 进程组，供 `task` 与（可选）ultraplan 复用。
- legacy 不动：conductor / ultraplan / btw 行为不变，feature gate（`GA_SUBAGENT_ENABLED`）隔离，文档标注为 legacy。
- **非破坏性**：无 BREAKING；纯新增 + feature gate。

## Capabilities

### New Capabilities

- `subagent-task`: 主循环 agent 经 `task` 工具扇出隔离子 agent 任务的能力——worktree 隔离、schema-校验结构化返回、批并发、超时/失败保留与启动 GC。

### Modified Capabilities

（无。现有 5 个 spec——`interactive-setup` / `skill-memory-integration` / `skill-discovery` / `memory-sop-format` / `mcp-client`——的 spec 级行为不变；本变更为纯新增能力，不改其需求。）

## Impact

- **代码**：`ga.py`（+`do_task`）、`agent_loop.py`（无改；handler 已有 `client`）、`agentmain.py`（+子模式装载 `submit_result`）、`assets/tools_schema.json`(+`_cn.json`)（+`task`、+子模式 `submit_result`）、新模块 `plugins/subagent_manager.py`、`assets/subprocess_worker.py`、`.gitignore`（+`.ga/`）。
- **API**：主循环 agent 工具表面 +1（`task`）；子 agent 工具表面 +1（`submit_result`，仅子模式注入）。
- **依赖**：无新第三方强依赖；schema 校验倾向 `jsonschema`（若已在 deps）否则最小手写校验，design 阶段定。
- **系统**：git worktree（本机 git 即可）；`.ga/worktrees/` 临时目录。
- **legacy**：conductor / ultraplan / btw 不变。
