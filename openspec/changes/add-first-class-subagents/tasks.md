## 1. 基础设施

- [ ] 1.1 查 `pyproject.toml` 是否已含 `jsonschema`;定 schema 校验方案(`jsonschema` 或最小手写校验)——解决 design Open Question
- [ ] 1.2 `.gitignore` 加 `.ga/`(若未含)
- [ ] 1.3 建 `plugins/subagent_manager.py`、`assets/subprocess_worker.py` 空骨架(含 docstring)

## 2. SubagentManager 核心(worktree + subprocess + 结果通道)

- [ ] 2.1 `SubagentManager.run_single(desc, schema, base_ref, model, tools, timeout_s)`:建 worktree(`git worktree add --detach .ga/worktrees/<uuid> <base_ref>`)
- [ ] 2.2 subprocess spawn `agentmain.py`(cwd=worktree,env `GA_TASK_MODE=isolated`+`GA_TASK_RESULT_SCHEMA=<json>`+`GA_TASK_TOOLS=<subset>`,独立进程组)
- [ ] 2.3 join 后读 `.ga/worktrees/<uuid>/.ga_task_result.json`、按 schema 校验
- [ ] 2.4 成功→`git worktree remove --force`+rmdir;失败/超时→保留入 `retained[]`

## 3. 批并发 / 超时 / 进程组 / GC

- [ ] 3.1 `run_batch(tasks, max_concurrent)`:ThreadPoolExecutor 扇出 N 个 `run_single`,返回有序结果列表
- [ ] 3.2 `max_concurrent`(默认 4,env `GA_SUBAGENT_MAX_CONCURRENT`)读取
- [ ] 3.3 超时:进程组 kill(POSIX `start_new_session`/Windows `CREATE_NEW_PROCESS_GROUP`+`taskkill /T` 或等价),标 `timed_out`
- [ ] 3.4 `retained[]` 上限 `max_retained`(默认 16)GC
- [ ] 3.5 启动 GC:扫 `.ga/worktrees/*` prune 无活进程占用的孤儿

## 4. 子模式 schema 强制(submit_result)

- [ ] 4.1 `assets/tools_schema.json`(+`_cn.json`)加 `submit_result` 工具条目(参数即返回对象)
- [ ] 4.2 `agentmain.py` 子模式(`GA_TASK_MODE=isolated`)装载 `submit_result`、约束 `tools` 子集(白名单+强制含 `submit_result`)、系统提示强制终末调用
- [ ] 4.3 `submit_result` 执行器:按 `GA_TASK_RESULT_SCHEMA` 校验参数,不过则返回错误让子 agent 重试;合规则写 `.ga_task_result.json`+exit 0

## 5. task 工具 + do_task 接线

- [ ] 5.1 `assets/tools_schema.json`(+`_cn.json`)加 `task` 工具条目(13 项;参数 `description`/`result_schema` 必填 + `base_ref`/`model`/`tools`/`timeout_s` 可选;收单或列表)
- [ ] 5.2 `ga.py` `do_task()`(gen-style):解析参数→单任务调 `run_single`、列表调 `run_batch`→yield StepOutcome(校验结果对象)
- [ ] 5.3 feature gate `GA_SUBAGENT_ENABLED`:gate off 时工具 schema 过滤不暴露 `task`

## 6. 共享 spawn 管线提炼(可选复用)

- [ ] 6.1 从 `ga_ultraplan._subagent`/`parallel` 提炼共享逻辑到 `subprocess_worker.py`;`task` 复用;ultraplan 迁移为可选(非强制,标 follow-up)

## 7. 测试

- [ ] 7.1 单测 `subagent_manager`(`run_single`/`run_batch`/worktree 建删/`retained`/GC)——可 mock subprocess
- [ ] 7.2 单测 `submit_result` 校验器(合规/非合规/缺失 schema)
- [ ] 7.3 集成 T1(单任务端到端:monkeypatch 子 agent 产出合规 result→worktree 建起/清理/父直读对象)
- [ ] 7.4 集成 T2(批模式:N 任务→N worktree 并行→返回 N 结果、受 `max_concurrent` 限)
- [ ] 7.5 集成 T3(schema 强制:不调 `submit_result`/非合规→failed 保留 worktree)
- [ ] 7.6 集成 T4(兄弟隔离:两 worker 改同名文件互不干扰)
- [ ] 7.7 集成 T5(超时:进程组 kill+`timed_out`+保留)
- [ ] 7.8 集成 T6(legacy 不破:gate off→无 `task`;conductor/ultraplan/btw 行为不变)
- [ ] 7.9 deps-complete env(Windows `uv pip install -e ".[ui]"`)跑全部;`ruff check` 新代码 0 违规;`pytest tests/` 不引入新失败

## 8. 文档

- [ ] 8.1 `AGENTS.md` 登记 `task`/`submit_result` 工具与 `GA_SUBAGENT_ENABLED` gate(若需)
- [ ] 8.2 标 conductor/ultraplan/btw 为 legacy(代码注释或 docs)
- [ ] 8.3 更新 `HANDOFF.md` 或新增 design 摘要(交接用)
