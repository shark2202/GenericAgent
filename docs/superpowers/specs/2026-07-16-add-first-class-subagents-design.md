---
comet_change: add-first-class-subagents
role: technical-design
canonical_spec: openspec
---

# Design Doc — First-Class Subagents (`task` 工具)

> 本文档是对 open 阶段 `openspec/changes/add-first-class-subagents/design.md`(高层框架,D1–D10)的**深度技术细化**。高层决策、方案选型、备选否决见 open design.md;本文聚焦实现设计、技术风险、边界条件、测试策略。规范事实源仍为 OpenSpec delta spec `specs/subagent-task/spec.md`。

## 1. Context & Deepening Scope

GA 主循环(`agent_loop.py`)对一回合内多个 tool_call **顺序派发**(`for ii, tc in enumerate(tool_calls): gen = handler.dispatch(...)`,见 `agent_loop.py:75-82`),逐个 await+drain。现有三种子 agent 机制(进程内线程 `conductor.SubagentPool` / 子进程 `ga_ultraplan._subagent` / 侧问 `/btw`)均未达 omp first-class:无 worktree 隔离、返回散文、主循环无统一 `task` 工具自扇出。

本设计补齐:**一等公民 `task` 工具** → 父 agent 扇出 → 每 worker 独立 git worktree 子进程 → 经注入的 `submit_result` 终末工具产出 schema-校验结构化对象 → 父直读(无散文)。纯新增 + feature gate,legacy 不破。

**本文深化(相对 open design.md):**(a) `submit_result` 终末性机制(复用 `should_exit` 硬终止,非软提示);(b) schema 校验用手写最小校验器(零新依赖);(c) 模块置于**仓库根级** `subagent_manager.py`(服务而非 hook,纠正 open design.md 的 `plugins/` 提法);(d) `do_task` 批模式 yield 形态(单 tool_call 产 1 个 StepOutcome,内含有序结果列表);(e) 跨平台进程组 kill 细节;(f) `agentmain.py` 子模式 env 接线(全新代码,无 `GA_` 处理现状)。

## 2. Goals / Non-Goals

见 open design.md。简述:Goals = worktree 隔离 + schema 校验结构化返回 + 批并发 + 健壮生命周期 + legacy 不破;Non-Goals = 不替 legacy、无 live steering(v1)、无 advisor/collab、不自动 merge。

## 3. Architecture

### 3.1 模块拓扑(纠正 open design.md)

```
仓库根/
├── ga.py                      # +do_task(gen-style) ; __init__ 实例化 self._subagent_mgr
├── subagent_manager.py        # NEW,根级(SubagentManager 单例:run_single/run_batch/GC/retained)
├── assets/
│   ├── tools_schema.json     # +task(13)+ +submit_result(子模式注入项)
│   ├── tools_schema_cn.json  # 同上
│   ├── subprocess_worker.py  # NEW,提炼自 ga_ultraplan(subprocess+worktree+result-file+timeout+pgroup)
│   └── ga_ultraplan.py       # legacy,可选迁移复用(非强制)
├── agentmain.py              # +子模式 env 接线(GA_TASK_MODE / GA_TASK_RESULT_SCHEMA / GA_TASK_TOOLS)
└── .gitignore                # +.ga/  (现状含 .ga_data/ 不含 .ga/)
```

**`subagent_manager.py` 置根级而非 `plugins/`**:`plugins/` 经 `plugins/hooks.py:46 discover_and_load()` 自动装载为**事件 hook**(agent_after/tool_after/turn_after);而 SubagentManager 是 `do_task` **显式调用的服务**,不订阅事件,故不入 `plugins/`。它在 `ga.py.__init__` 实例化为 `self._subagent_mgr`,构造时即跑启动 GC(扫 `.ga/worktrees/*` 清孤儿)。

### 3.2 数据流(单任务)

```
父 agent LLM ─tool_call(task, {description,result_schema,...})─▶ ga.py do_task()
                                                                     │
                                                                     ▼
                                          subagent_manager.run_single(...)
                                                  │
                          ┌───────────────────────┼──────────────────────┐
                          ▼                       ▼                      ▼
              git worktree add --detach   subprocess spawn          (注册到 registry)
              .ga/worktrees/<uuid> <base>  agentmain.py              state=running
                          │              cwd=<worktree>
                          │              env GA_TASK_MODE=isolated
                          │                  GA_TASK_RESULT_SCHEMA=<json>
                          │                  GA_TASK_TOOLS=<subset∪submit_result>
                          │              独立进程组
                          ▼
              子 agent loop............... tool_call(submit_result, {result obj})
                                            │
                                            ▼
                                  submit_result 执行器:
                                    手写校验 vs result_schema
                                    ✓ → 写 .ga_task_result.json + should_exit=True
                                    ✗ → 返回校验错误(子 agent 重试,受 max_turns 限)
                          │
              子进程 exit 0 ◀────────────┘ (loop 因 should_exit 终止)
                          │
                          ▼
              父 join: 读 .ga_task_result.json → 再校验 schema
                          │
                ┌─────────┴─────────┐
                ▼                   ▼
            schema✓             schema✗/非0退出/超时
            state=succeeded      state=failed/timed_out
            git worktree remove  worktree 保留入 retained[]
            --force + rmdir
                          │
                          ▼
              do_task yield StepOutcome(result=校验通过对象)
```

### 3.3 数据流(批模式,关键深化 D2)

```
父 agent ─tool_call(task, [{t1},{t2},...,{tN}])─▶ do_task()  [单次 tool_call]
                                                        │
                                  run_batch(tasks, max_concurrent=4)
                                                        │
                              ThreadPoolExecutor(max_workers=4) 并行调度
                              每个 future = 一个 run_single(独立 worktree+子进程)
                                                        │
                              并发上限=4,超出排队(不拒绝);N≤4 全并行
                                                        │
                              as_completed → 收集结果 → 按输入 index 排序
                                                        │
                              yield **ONE** StepOutcome(result=[r1,r2,...,rN] 有序列表)
```

**关键**:主循环串行派发 → 跨调用 ThreadPoolExecutor 死重(D2);故批模式必须在**单次 tool_call 内部**扇出 N 子进程。批模式产 **1 个 StepOutcome**(内含有序列表),而非 N 个 outcome——主循环一次 drain 即得全部结果。

## 4. Detailed Design

### 4.1 `subagent_manager.SubagentManager`

```python
class SubagentManager:
    def __init__(self, root, max_concurrent=4, max_retained=16):
        self.root = Path(root)
        self.wt_root = self.root / ".ga" / "worktrees"
        self.max_concurrent = int(os.environ.get("GA_SUBAGENT_MAX_CONCURRENT", max_concurrent))
        self.max_retained = max_retained
        self.registry = {}        # uuid -> WorkerState
        self.retained = []        # [(uuid, path, reason, ts)...] 失败保留
        self._startup_gc()        # 构造即清孤儿
```

- **`run_single(desc, schema, base_ref=None, model=None, tools=None, timeout_s=1800)`**:建 worktree(`git worktree add --detach <path> <base_ref or HEAD>`)→ spawn(§4.4)→ join → 读 result 文件 + 手写校验 → 成功清理/失败保留。返回 `WorkerResult(state, result|error, worktree_path)`。
- **`run_batch(tasks, max_concurrent=None)`**:`ThreadPoolExecutor(max_workers=max_concurrent or self.max_concurrent)`;`ex.map(run_single, tasks)` 收集 → 按 index 排序(保输入顺序)→ 返回有序 `WorkerResult` 列表。
- **`_startup_gc()`**:扫 `wt_root/*`;对每个目录查是否被活进程占用(无 pid 文件或 pid 不活)→ `git worktree prune`(清理 git 内部引用)+ `shutil.rmtree`。`max_retained` 超限删最旧。
- **registry 状态机**:`pending → running → succeeded | failed | timed_out → cleaned | retained`。

### 4.2 `do_task`(ga.py,gen-style,镜像 `do_skill_manage`)

```python
def do_task(self, args, response, index=0, tool_num=1):
    # gen-style: yield StepOutcome
    if not os.environ.get("GA_SUBAGENT_ENABLED"):
        yield StepOutcome(..., error="task disabled (GA_SUBAGENT_ENABLED unset)")
        return
    tasks = args["tasks"] if "tasks" in args else [args]   # 统一成 list
    results = self._subagent_mgr.run_batch([
        SubagentTask(t["description"], t["result_schema"], t.get("base_ref"),
                     t.get("model"), t.get("tools"), t.get("timeout_s", 1800))
        for t in tasks
    ])
    # 批模式:1 个 StepOutcome,内含有序列表
    ok = [r.result for r in results if r.state == "succeeded"]
    if len(tasks) == 1:
        yield StepOutcome(result=results[0].result if results[0].state=="succeeded"
                          else None, error=results[0].error)
    else:
        yield StepOutcome(result=ok,  # 有序列表(按输入 index)
                          error=[{"index":i,"reason":r.error} for i,r in enumerate(results) if r.state!="succeeded"] or None)
```

> 单任务时返回单对象,批模式返回有序列表;部分失败以 `error` 槽列出失败 index+reason,成功项仍在 `result` 列表中(父可容错消费)。

### 4.3 `submit_result` 注入与终末性(D3 深化)

**机制(选定)**:子模式给子 agent 注入 `submit_result` 工具,其执行器在校验通过后 **置 `should_exit=True` 复用既有 `do_exit`/`should_exit` 通路**,使 `agent_loop` 在该 tool_call 后**立即终止子 loop**(不再调 LLM)。这是硬保证「终末=结果」「无散文」。

- 备选 (b) 普通工具+系统提示软约束:LLM 可能在 `submit_result` 后继续输出散文 → 「无散文」成空头支票,否决。
- 备选 (c) 抑制下一次 LLM 调用:仍需新机制且时序脆弱,否决。

```python
# 子模式 submit_result 执行器(伪码)
def do_submit_result(self, args, response, index=0, tool_num=1):
    schema = json.loads(os.environ["GA_TASK_RESULT_SCHEMA"])
    err = validate(args["result"], schema)   # §4.5 手写校验
    if err:
        yield StepOutcome(error=f"schema validation failed: {err}")  # 子 agent 重试
        return
    wt = Path(os.environ["GA_TASK_WORKTREE"])
    (wt / ".ga_task_result.json").write_text(json.dumps(args["result"]))
    self.should_exit = True    # ← 复用既有 do_exit 通路,硬终止子 loop
    yield StepOutcome(result="result submitted")
```

**重试契约**:非合规 → 工具返回校验错误 → 子 agent 被 re-prompt → 重试上限 = 子 agent `max_turns`(沿用既有);耗尽仍未合规 → 子进程非 0 退出 → 父端标 `failed` + 保留 worktree(§4.6)。不调 `submit_result` 即 max_turns 耗尽 → 同 `failed` 通路。

### 4.4 子进程 spawn 与进程组(跨平台,D7)

```python
import subprocess, sys, os
popen_kwargs = dict(
    cwd=str(worktree),
    env={**os.environ,
         "GA_TASK_MODE": "isolated",
         "GA_TASK_RESULT_SCHEMA": json.dumps(schema),
         "GA_TASK_WORKTREE": str(worktree),
         "GA_TASK_TOOLS": ",".join(caller_subset ∪ {"submit_result"})},
)
if os.name == "nt":
    popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
else:
    popen_kwargs["start_new_session"] = True
proc = subprocess.Popen([sys.executable, "agentmain.py", ...], **popen_kwargs)
```

**超时 kill**:
- POSIX:`os.killpg(os.getpgid(proc.pid), signal.SIGKILL)`(`start_new_session` 使子进程自成新 pg)。
- Windows:`subprocess.run(["taskkill", "/T", "/PID", str(proc.pid)])`(`CREATE_NEW_PROCESS_GROUP` + `/T` 杀树)。
- 超时后 `state=timed_out` + worktree 保留入 `retained[]`。

### 4.5 手写 schema 校验器(OQ1,零依赖)

`jsonschema` 不在 `pyproject.toml` deps(已核实)。采用最小手写校验器,覆盖子 agent 结果常见形态:

```python
def validate(obj, schema):
    t = schema.get("type")
    if t == "object":
        for k in schema.get("required", []):
            if k not in obj: return f"missing required '{k}'"
        props = schema.get("properties", {})
        for k, v in obj.items():
            if k in props:
                e = validate(v, props[k]);  # 递归
                if e: return f".{k}: {e}"
            elif schema.get("additionalProperties") is False:
                return f"extra key '{k}'"
        return None
    if t == "array":
        if not isinstance(obj, list): return "not array"
        it = schema.get("items"); 
        return next((f"[{i}]: {validate(x,it)}" for i,x in enumerate(obj) if validate(x,it)), None) if it else None
    if t == "string":  return None if isinstance(obj, str) else "not string"
    if t == "number":  return None if isinstance(obj, (int,float)) else "not number"
    if t == "boolean": return None if isinstance(obj, bool) else "not boolean"
    if "enum" in schema and obj not in schema["enum"]: return "not in enum"
    return None
```

**不支持的 schema**:oneOf/anyOf/allOf/$ref/pattern/format。对子 agent 返回的扁平结构对象足够;若 result_schema 含复杂结构,父 agent 调 `task` 时自行改用更简 schema——YAGNI,不为 1% 引入 `jsonschema` 依赖。

### 4.6 结果通道(D5)

`.ga/worktrees/<uuid>/.ga_task_result.json`:子进程 `submit_result` 执行器写后 `exit 0`;父 `join` 后读该文件 + **再次**手写校验(双重保险:防子进程绕过 `submit_result` 直写)。文件缺失/非法 JSON/schema 不符 → `failed`,保留 worktree。

### 4.7 feature gate(D10)

`GA_SUBAGENT_ENABLED`:未设/0 → `tools_schema` 过滤不暴露 `task` 给主循环 agent,legacy 完全不变。`do_task` 内再次校验 gate(纵深防御)。`submit_result` 仅子模式注入,主循环 agent 看不到。

### 4.8 `agentmain.py` 子模式 env 接线

现状:`agentmain.py:24` 把 `tools_schema` 当字符串加载,**无任何 `GA_` env 处理**(已核实)。子模式新增分支:

```python
if os.environ.get("GA_TASK_MODE") == "isolated":
    result_schema = json.loads(os.environ["GA_TASK_RESULT_SCHEMA"])
    worktree = os.environ["GA_TASK_WORKTREE"]
    allowed = set(os.environ.get("GA_TASK_TOOLS","").split(","))   # caller subset ∪ submit_result
    # 装载 submit_result 工具 + 按 allowed 过滤工具集
    # 系统提示强制:终末必须调用 submit_result;参数即返回对象
```

工具子集语义(OQ4)= **caller 指定 ∪ 强制 `submit_result`**(即父不能把 `submit_result` 排除掉,否则 schema 强制失效)。

## 5. Error Handling / Boundary Conditions

| 场景 | 处理 |
|---|---|
| `git worktree add` 失败(base_ref 不存在/路径冲突) | `pending → failed`,不 spawn |
| 子进程非 0 退出 | 读不到 result 文件 → `failed` + 保留 |
| result 文件存在但 schema 不符 | `failed` + 保留(双重校验第二道兜底) |
| 子 agent 不调 `submit_result` → max_turns 耗尽 | 子进程非 0 退出 → `failed` + 保留 |
| `submit_result` 非合规 → 重试至 max_turns 仍失败 | 同上 |
| 超时 | 进程组 kill → `timed_out` + 保留 |
| 并发 > max_concurrent | 排队等待,不拒绝 |
| `retained[]` 超 max_retained(16) | 删最旧 |
| 启动遇孤儿 worktree | prune + rmdir |
| gate off | `task` 不暴露;`do_task` 返 error(纵深防御) |
| 父进程 crash 留孤儿 | 下次启动 GC 清理 |

## 6. Test Strategy

**单测(可 mock subprocess,无需 deps-complete env)**:
- `subagent_manager`:worktree 建删 / `run_single` happy path(mock 子进程写 result) / `run_batch` 并发与排序 / `retained` 上限 / `_startup_gc`。
- `validate`:object/array/scalar 合规与非合规、`required` 缺失、`additionalProperties:false`、enum。

**集成(类比 hermes Step 5b,需 deps-complete env:Windows `uv pip install -e ".[ui]"`)**:
- T1 单任务端到端:monkeypatch 子 agent 产合规 result → worktree 建起/清理/父直读对象。
- T2 批模式:N 任务 → N worktree 并行 → 返回 N 结果、受 `max_concurrent` 限、保序。
- T3 schema 强制:不调 `submit_result` / 非合规 → `failed` + 保留 worktree。
- T4 兄弟隔离:两 worker 改同名文件互不干扰、无孤儿编辑。
- T5 超时:进程组 kill + `timed_out` + 保留。
- T6 legacy 不破:gate off → 无 `task`;conductor/ultraplan/btw 行为不变。

**验收**:`ruff check` 新代码 0 违规;`pytest tests/` 不引入新失败(已知 baseline flakiness 不计)。

## 7. Design Decisions Deepened(对 open design.md 的细化/纠正)

| 决策 | open design.md | 本 Doc 深化 |
|---|---|---|
| D3 终末性 | 注入 `submit_result` | **复用 `should_exit` 硬终止子 loop**(非软提示);重试上限=子 `max_turns` |
| OQ1 schema 校验 | jsonschema or 手写 | **手写最小校验器**(零新依赖;不支持 oneOf/$ref) |
| OQ2 max_concurrent | 倾向 4 | **4**(env `GA_SUBAGENT_MAX_CONCURRENT`) |
| OQ3 列表返回 | list vs dict | **有序 list**(index=输入序) |
| OQ4 tools 子集 | 倾向后者 | **caller subset ∪ 强制 submit_result** |
| 模块位置 | `plugins/subagent_manager.py` | **根级 `subagent_manager.py`**(服务非 hook,纠正) |
| 批 yield 形态 | (未明) | 单 tool_call 批模式产 **1 StepOutcome**(内含有序列表) |
| 进程组 kill | (平台分支) | POSIX `start_new_session`+`killpg`;Win `CREATE_NEW_PROCESS_GROUP`+`taskkill /T` |
| .ga/ gitignore | (待查) | 现状 `.gitignore` 含 `.ga_data/` 不含 `.ga/` → **需加 `.ga/`** |

## 8. Risks(深化)

- **丢 live steering**:v1 父不能中途改 child → 缓解:后续 control-file/pipe(v1.5)。
- **子 agent 上下文不继承父对话**:每 worker 全新实例读 nearest AGENTS.md → 缓解:`description` 显式传必要上下文。
- **worktree 磁盘占用**:失败保留 16 个上限;超限 GC 删最旧。
- **手写校验器盲区**:不支持复杂 schema → 父 agent 调 `task` 时自用简 schema;YAGNI 不引入 jsonschema。
- **Windows 进程组语义差**:依赖 `taskkill /T` 杀树;CI 需覆盖 T5 on Windows。
- **子进程 crash 中途**:result 文件半写 → 第二道校验 + `failed` + 保留。

## 9. Out of Scope / v1.5

- live steering / 通信中继(v1.5:control-file/pipe)。
- 自动 merge 子 agent 代码改动到主干(父决定,提供 `commit_sha`+`worktree_path` 槽位即可)。
- omp 的 advisor / collab / hindsight 等其他能力。
- ultraplan 迁移复用 `subprocess_worker.py`(可选 follow-up,非强制)。

## 10. Mapping to OpenSpec Spec

delta spec `specs/subagent-task/spec.md` 的 7 Requirement / 11 Scenario 已覆盖本设计所有决策;**无 Spec Patch**(需求层无需变更,本文为实现深化)。校验器/模块位置/批 yield 形态属实现细节,不进 spec。
