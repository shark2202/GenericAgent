## Context

GA 主循环(`agent_loop.py`)对 LLM 一回合返回的多个 tool_call 是**顺序派发**的(`for ii, tc in enumerate(tool_calls): gen = handler.dispatch(...)`,见 `agent_loop.py:75-82`),逐个 await+drain。现有三种子 agent 机制——`frontends/conductor.py` `SubagentPool`(进程内 `GenericAgent()` 守护线程 + FastAPI HTTP API,返回 display queue 文本)、`assets/ga_ultraplan.py` `_subagent`(`subprocess.run` agentmain.py,prompt+stdout 文件,`parallel()` 扇出)、`frontends/btw_cmd.py` `/btw`(侧问)——均未达 omp first-class:无 worktree 隔离、返回散文、主循环 agent 无统一 `task` 工具自扇出。本设计补齐该能力,纯新增 + feature gate,不动 legacy。

## Goals / Non-Goals

**Goals:**
- 主循环 agent 经 `task` 工具扇出隔离子 agent,每 worker 独立 git worktree(对齐 omp:无共享工作目录 / 无孤儿编辑)。
- 子 agent 返回 **schema-校验的结构化对象**,父直读、无散文解析(对齐 omp: no prose to parse)。
- 批并发:一次 `task` 调用传 N 任务 → N 隔离 worktree 并行,受 `max_concurrent` 限。
- 超时/失败/启动 GC 的健壮生命周期管理。
- legacy 不破。

**Non-Goals:**
- 不替换/废弃 conductor / ultraplan / btw(feature gate 隔离,文档标 legacy)。
- 不做 live steering / 通信中继(v1;父不能中途改 child)。
- 不做 omp 的 advisor / collab / hindsight 等其他能力。
- 不自动 merge 子 agent 的代码改动到主干(merge 由父决定)。

## Decisions

**D1 — 子进程 + worktree 隔离(非进程内线程)**
每 `task` 调用 subprocess 起 `agentmain.py`,`cwd=其独立 git worktree`。理由:进程内多线程并发 `chdir` 有竞争(线程间共享 cwd,不安全);子进程天然隔离 cwd;对齐 omp per-worker worktree。备选:进程内线程+worktree(并发 chdir race,脆弱,否决)、混合 subprocess+HTTP steering(工程量最大,live steering 留 v1.5,否决)。

**D2 — 批模式:`task` 既收单任务也收列表**
关键事实:主循环串行派发(`agent_loop.py:75-82`),跨调用 `ThreadPoolExecutor` 永远跑不到(一次 `task` 阻塞到 worker 完成才返回,并发恒 1)。故 `task` 必须支持一次调用传 N 任务、工具内部 `ThreadPoolExecutor` 扇出 N 隔离 worktree 子进程。印证 `ga_ultraplan.py:201` `parallel(tasks,max_workers)` 早就走通的「一次 code_run 内部起 N subprocess」模式。备选:单任务+独立 `parallel` 工具(分裂 API,否决)、单任务无并发(并发层死重,否决)。

**D3 — schema 强制 = 注入 `submit_result` 终末工具**
子模式给子 agent 注入 `submit_result` 工具,子 agent **必须以调用它为终末动作**,参数即返回对象;工具侧按 `result_schema` 校验,不过则报错让子 agent 重试。硬保证 schema-conformant,对齐 omp `yield_result`。备选:解析终末 assistant message 为 JSON(不可靠:LLM 可能输出散文/前后缀,「无散文」成空头支票,否决)。

**D4 — 输出契约:数据-only 默认 + `commit_sha`/`worktree_path` 槽位,merge 由父决定**
worktree 用 `--detach`(避免分支堆积),结果默认是数据对象;若子 agent 产出了 git commit,在 result schema 槽位填 `commit_sha` + `worktree_path`,父可按需 cherry-pick。v1 不自动 merge(父上下文不足,危险)。备选:自动 merge(危险,否决)、只返数据不返 sha(丢取回路径,否决)。

**D5 — 结果通道 = 文件 `.ga_task_result.json`**
子进程把最终结果写到 `.ga/worktrees/<uuid>/.ga_task_result.json` 后 exit 0;父 join 后读文件、按 schema 校验。抗 crash(部分写可检测)、不与 stdout 调试输出混淆。备选:stdout JSON(子 agent 调试日志会污染 stdout,否决)。

**D6 — 并发模型 = `ThreadPoolExecutor` 包 `subprocess.run`**
每 worker 一线程等 `join`;subprocess 本身 GIL-free,线程仅做阻塞等待。简单。备选:asyncio subprocess(主循环非 async,适配成本高,否决)。

**D7 — 超时 = 进程组 kill**
子进程在独立进程组 spawn(POSIX `start_new_session=True`;Windows `CREATE_NEW_PROCESS_GROUP`),`timeout_s` 到点 group-kill 一次性收掉 agentmain 及其派生的 bash/git 子进程,标 `timed_out`,保留 worktree。

**D8 — 失败保留 worktree + 启动 GC 孤儿**
failed/timed_out → worktree 保留入 registry `retained[]` 供排查;`max_retained` 默认 16,超限删最旧。parent 启动扫 `.ga/worktrees/*`,无活进程占用的孤儿 `git worktree prune` + 删目录。

**D9 — 共享 `assets/subprocess_worker.py`**
从 ultraplan `_subagent`/`parallel` 模式提炼共享 spawn 管线(subprocess+worktree+结果文件+超时+进程组),供 `task` 复用;ultraplan 可选迁移复用(非强制,避免重复造轮子)。

**D10 — legacy 不动 + feature gate(`GA_SUBAGENT_ENABLED`)**
纯新增,默认 off→灰度→on。Approach 2(全替换三机制)blast radius 大,已否决。

## Risks / Trade-offs

- [丢 live steering] → v1 父不能中途改 child;缓解:后续加 control-file/pipe(v1.5)。
- [子 agent 上下文不继承父对话] → 每 worker 全新 GA 实例读 nearest AGENTS.md;缓解:`description` 显式传必要上下文。
- [worktree 磁盘占用] → `max_retained=16` GC。
- [`submit_result` 可能不被调用] → 子模式系统提示强制 + `timeout_s` 兜底 + 不调则 failed 保留 worktree。
- [Windows 进程组 kill 语义差] → `taskkill /T` 或 NPG,cross-platform 分支。
- [jsonschema 依赖未定] → 见 Open Questions。

## Migration Plan

纯新增 + feature gate,无数据迁移。`GA_SUBAGENT_ENABLED=1` 灰度 → on。rollback = 关 gate;遗留 worktree 由启动 GC 清理。tasks 完成后进 build 阶段,先单测(`subagent_manager`/`submit_result` 校验)再端到端(T1/T3 类比 hermes Step 5b 的 deps-complete env)。

## Open Questions

- schema 校验:用 `jsonschema`(若已在 deps)还是最小手写校验?(build 前查 `pyproject.toml`)
- `max_concurrent` 默认值最终定值(倾向 4)。
- `task` 列表模式返回值形态:有序 list vs 按 `id`/`name` 的 dict。
- 子 agent `tools` 子集白名单语义:是「仅这些」还是「这些 + 强制含 `submit_result`」?(倾向后者)
- `.ga/` 是否已在 `.gitignore`(否则需加)。
