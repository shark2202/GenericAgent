---
change: add-first-class-subagents
design-doc: docs/superpowers/specs/2026-07-16-add-first-class-subagents-design.md
base-ref: 8d7878083f94a35efe3fa163e4439451b74d0658
---

# First-Class Subagents（`task` 工具）实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 给 GA 主循环 agent 补一等公民 `task` 工具：扇出各自独立 git worktree 中的子 agent，返回 schema-校验的结构化结果（父直读、无散文），批并发，对齐 omp first-class subagents；纯新增 + feature gate，legacy 不破。

**Architecture:** `task` 工具（gen-style，镜像 `do_skill_manage`）→ 根级 `subagent_manager.SubagentManager`（服务非 hook，`ga.py.__init__` 实例化为 `self._subagent_mgr`）→ 每 worker `git worktree add --detach` + subprocess 起 `agentmain.py`（cwd=worktree、独立进程组）→ 子模式（env 触发）注入 `submit_result` 终末工具，校验通过即 `should_exit=True` 复用既有 `do_exit`/`should_exit` 通路硬终止子 loop → 父 join 读 `.ga_task_result.json` 双重校验 → 批模式单次 tool_call 内 `ThreadPoolExecutor` 扇出 N 子进程，产 1 个 StepOutcome（内含有序列表）。

**Tech Stack:** Python 3.11/3.12、`subprocess` + `concurrent.futures.ThreadPoolExecutor`、`git worktree`、手写最小 schema 校验器（零新依赖）、既有 `agent_loop.StepOutcome` dataclass。

**Design Doc（权威技术设计）:** `docs/superpowers/specs/2026-07-16-add-first-class-subagents-design.md`（10 节）。规范事实源：OpenSpec delta `openspec/changes/add-first-class-subagents/specs/subagent-task/spec.md`（7 Requirement / 11 Scenario）。本计划是其可执行分解，任务 ID 与 `openspec/changes/add-first-class-subagents/tasks.md`（8 组 / 31 项）一一对应。

---

## 已确认的关键决策（来自 brainstorm-summary.md，实现时必须遵守）

1. **schema 校验 = 手写最小校验器**（覆盖 `type`/`required`/`properties`/`items`/`enum`/`additionalProperties:false`）；`jsonschema` 不在 `pyproject.toml` deps，YAGNI 不引入。
2. **submit_result 终末性** = 成功 → `should_exit=True` → 复用既有 `do_exit`/`should_exit` 通路（`agent_loop.py:91`）立即终止子 agent_loop；硬保证「终末=结果」「无散文」。
3. **重试契约** = 非合规 → re-prompt 子 agent；上限 = 子 agent `max_turns`；耗尽 → `failed` + 保留 worktree。
4. **do_task 批 yield** = 单次 tool_call 批模式产 **1 个 StepOutcome**（`result` = 有序结果列表），不是 N 个 outcome。
5. **列表返回形态** = 有序 list（index = 输入顺序）。
6. **tools 子集语义** = caller 子集 ∪ 强制 `submit_result`（父不能排除 `submit_result`）。
7. **max_concurrent 默认 = 4**（env `GA_SUBAGENT_MAX_CONCURRENT` 可调）。
8. **Windows 进程组 kill** = `CREATE_NEW_PROCESS_GROUP` + `taskkill /T /PID`；POSIX = `start_new_session` + `os.killpg`。
9. **模块放置 = 根级 `subagent_manager.py`**（`ga.py.__init__` 导入实例化为 `self._subagent_mgr`，启动 GC 在 init 跑）；**NOT `plugins/`**——它是 `do_task` 显式调用的服务，不订阅事件 hook（`plugins/hooks.py:46 discover_and_load` 自动装载的是 hook）。

> ⚠️ 注意：`tasks.md` 1.3 原文写「建 `plugins/subagent_manager.py`」，但 design doc §3.1 与决策 9 已将其**纠正为根级**。本计划遵循 design doc（权威），1.3 落地为根级 `subagent_manager.py`，任务 ID 保留不变。

## 环境约束（重要）

- **deps-complete 环境（Windows `.venv`）**：`uv pip install -e ".[ui]"`。`ga.py` 依赖 `pywebview` 等，Linux 沙箱无法 `import ga.py` / 跑 `agentmain.py`。
- **[env-blocked]**（需 deps-complete env）：集成测试 **T1–T6 = 任务 7.3–7.8**，以及 **7.9**（deps-complete 跑全套 ruff/pytest）。这些任务在 Linux 沙箱**不可执行**，必须切到 Windows `.venv`。
- **[anywhere]**（任意环境可做）：全部代码编写（1.x–6.x、8.x）与单测 **7.1、7.2**（mock subprocess，不 import ga.py 主路径）。
- 每个任务标题末尾标 `[anywhere]` 或 `[env-blocked]`。

## 代码事实锚点（实现时核对签名）

- `StepOutcome(data, next_prompt=None, should_exit=False)` — dataclass，`agent_loop.py:7-10`。`should_exit=True` → `agent_loop.py:91-92` 立即 break 子 loop。
- `do_ask_user`（`ga.py:315-320`）= 干净的 `should_exit=True` 先例（`return StepOutcome(result, next_prompt="", should_exit=True)`）。
- `do_skill_manage`（`ga.py:471`）= gen-style 先例：`yield "..."` 文本片段，`return StepOutcome(...)`。
- `GenericAgentHandler.__init__(self, parent, last_history=None, cwd='./temp')` — `ga.py:268`。`do_task` 实例化为 `self._subagent_mgr` 的注入点。
- 主循环串行派发：`agent_loop.py:75` `for ii, tc in enumerate(tool_calls):` → 跨调用并发恒 1（D2 依据，批模式必须单 tool_call 内部扇出）。
- `agentmain.py:22-27` `load_tool_schema(suffix='')`：读 `assets/tools_schema{suffix}.json` 为字符串→`json.loads`→按 `BANNED_TOOLS` 过滤。**现状无任何 `GA_` env 处理**（子模式是全新分支）。
- `assets/tools_schema.json`：现状 12 个工具条目，shape `{type:"function", function:{name, description, parameters:{type, properties, ...}}}`。`task` 将是第 13 项。
- `.gitignore:170` = `.ga_data/`，**无 `.ga/`**。
- `ga_ultraplan._subagent`（`ga_ultraplan.py:165-178`）= 既有 `subprocess.run(agentmain.py, ...)` 模式（无 worktree、stdout 文件）；`parallel`（`:196`）= 既有 `ThreadPoolExecutor` 先例。任务 6.1 从此提炼。

## 验证命令总表

| 范围 | 命令 | 期望 | env |
|---|---|---|---|
| 新代码 lint | `ruff check plugins/ assets/subprocess_worker.py tests/test_subagent_manager.py tests/test_submit_result.py` | 0 违规 | anywhere |
| 单测 | `pytest tests/test_subagent_manager.py tests/test_submit_result.py -v` | 全绿 | anywhere（mock subprocess） |
| 集成 T1–T6 | `pytest tests/test_subagent_integration.py -v` | 全绿 | env-blocked |
| 全量回归 | `pytest tests/` | 不引入新失败（baseline flakiness 不计） | env-blocked |

> 注：`subagent_manager.py` 在仓库根级不在 `plugins/` 下，但 `ruff check plugins/` 覆盖 `assets/subprocess_worker.py` 调用到的 `plugins/hooks.py`（若新代码引用 hook）。根级 `subagent_manager.py` 单独 `ruff check subagent_manager.py`。

---

## 组 1：基础设施

### Task 1.1：定 schema 校验方案 `[anywhere]`

**Files:**
- 核对：`pyproject.toml`（`[project] dependencies` / `[project.optional-dependencies]`）
- 记录结论到：design doc 已定（手写），本任务仅核对事实并落 docstring

**Step 1：核对 `jsonschema` 是否在 deps**
Run: `grep -n "jsonschema" pyproject.toml`（或读 `[project]` 段）
Expected: 无匹配（brainstorm 已确认）。若有匹配则停止并回 design doc。

**Step 2：落结论**
在 `subagent_manager.py`（Task 1.3 创建）顶部 docstring 写明：「schema 校验 = 手写最小校验器；不引入 jsonschema（YAGNI）。覆盖 type/required/properties/items/enum/additionalProperties:false；不支持 oneOf/anyOf/allOf/$ref/pattern/format。」

**Done when:** `pyproject.toml` 无 `jsonschema`；`subagent_manager.py` docstring 记录结论。

### Task 1.2：`.gitignore` 加 `.ga/` `[anywhere]`

**Files:**
- Modify: `.gitignore`（在 L170 `.ga_data/` 后追加）

**Step 1：确认现状**
Run: `grep -n "^\.ga" .gitignore`
Expected: 仅 `.ga_data/`（L170），无 `.ga/`。

**Step 2：追加**
在 `.gitignore` 末尾 `.ga_data/` 之后追加：
```
# GA subagent worktrees
.ga/
```

**Done when:** `grep "^\.ga/" .gitignore` 命中；`git status` 显示 `.gitignore` modified。

### Task 1.3：建根级 `subagent_manager.py` 与 `assets/subprocess_worker.py` 空骨架 `[anywhere]`

> ⚠️ 模块放置纠正：design doc §3.1 / 决策 9 = **根级 `subagent_manager.py`**（非 `plugins/`）。`tasks.md` 原文 `plugins/subagent_manager.py` 已纠正。任务 ID 1.3 保留。

**Files:**
- Create: `subagent_manager.py`（仓库根级）
- Create: `assets/subprocess_worker.py`

**Step 1：建 `subagent_manager.py` 骨架**
```python
"""First-class subagent manager (task tool service, NOT a plugins/ hook).

根级模块：被 ga.py.__init__ 实例化为 self._subagent_mgr，由 do_task 显式调用。
不入 plugins/（plugins/hooks.py:46 discover_and_load 装载的是事件 hook，本服务不订阅事件）。

schema 校验 = 手写最小校验器（type/required/properties/items/enum/additionalProperties:false）；
不引入 jsonschema（不在 pyproject.toml deps，YAGNI）。不支持 oneOf/$ref/pattern/format。

Design: docs/superpowers/specs/2026-07-16-add-first-class-subagents-design.md
Spec:   openspec/changes/add-first-class-subagents/specs/subagent-task/spec.md
"""
import os, json, shutil, subprocess, sys, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

WT_ROOT_NAME = ".ga"
WORKTREES_DIR = "worktrees"
RESULT_FILENAME = ".ga_task_result.json"
DEFAULT_MAX_CONCURRENT = 4
DEFAULT_MAX_RETAINED = 16
DEFAULT_TIMEOUT_S = 1800


class SubagentManager:
    def __init__(self, root, max_concurrent=DEFAULT_MAX_CONCURRENT,
                 max_retained=DEFAULT_MAX_RETAINED):
        raise NotImplementedError  # Task 2.x / 3.x 填充


def validate(obj, schema):
    """手写最小 JSON-schema 校验器（零依赖）。返回 None=合规，str=错误信息。Task 4.x 实现。"""
    raise NotImplementedError
```

**Step 2：建 `assets/subprocess_worker.py` 骨架**
```python
"""共享子进程 spawn 管线（subprocess + worktree + result-file + 超时 + 进程组）。

提炼自 ga_ultraplan._subagent/parallel（ga_ultraplan.py:165,196），供 task 工具复用；
ultraplan 迁移复用为可选 follow-up（Task 6.1），非强制。
跨平台进程组：POSIX start_new_session + os.killpg；Windows CREATE_NEW_PROCESS_GROUP + taskkill /T /PID。
"""
import os, sys, json, signal, subprocess
from pathlib import Path

def spawn_agentmain(worktree, *, schema, tools_subset, model=None, timeout_s=1800):
    """起 agentmain.py 子进程（cwd=worktree，独立进程组，GA_TASK_* env 接线）。Task 2.2 实现。"""
    raise NotImplementedError

def kill_process_group(proc):
    """跨平台杀整个进程组。Task 3.3 实现。"""
    raise NotImplementedError
```

**Step 3：lint 骨架**
Run: `ruff check subagent_manager.py assets/subprocess_worker.py`
Expected: 0 违规（骨架仅含 import + NotImplementedError）。

**Done when:** 两文件存在、含 docstring、`ruff` 0 违规、`python -c "import ast; ast.parse(open('subagent_manager.py').read()); ast.parse(open('assets/subprocess_worker.py').read())"` 无语法错。

---

## 组 2：SubagentManager 核心（worktree + subprocess + 结果通道）

### Task 2.1：`run_single` 建独立 worktree `[anywhere]`

**Files:**
- Modify: `subagent_manager.py`（填充 `SubagentManager.__init__` + `run_single` 的 worktree 段）
- Test: `tests/test_subagent_manager.py`（Create，本任务先写 worktree 建删用例）

**关键签名：**
```python
@dataclass
class WorkerResult:
    uuid: str
    state: str          # pending|running|succeeded|failed|timed_out|cleaned|retained
    result: Any = None
    error: Optional[str] = None
    worktree_path: Optional[str] = None

class SubagentManager:
    def __init__(self, root, max_concurrent=DEFAULT_MAX_CONCURRENT,
                 max_retained=DEFAULT_MAX_RETAINED):
        self.root = Path(root).resolve()
        self.wt_root = self.root / WT_ROOT_NAME / WORKTREES_DIR
        self.max_concurrent = int(os.environ.get("GA_SUBAGENT_MAX_CONCURRENT", max_concurrent))
        self.max_retained = max_retained
        self.registry: dict[str, WorkerResult] = {}
        self.retained: list[tuple] = []   # (uuid, path, reason, ts)
        self.wt_root.mkdir(parents=True, exist_ok=True)
        self._startup_gc()   # Task 3.5 实现，此处先 stub

    def _new_worktree(self, base_ref) -> str:
        """git worktree add --detach .ga/worktrees/<uuid> <base_ref>。返回 worktree path。"""
        wid = uuid.uuid4().hex
        wt = self.wt_root / wid
        base = base_ref or "HEAD"
        r = subprocess.run(["git", "worktree", "add", "--detach", str(wt), base],
                           cwd=str(self.root), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"worktree add failed: {r.stderr}")
        return str(wt)
```

**Step 1：写失败测试** — 临时 git repo + `SubagentManager(tmp).run_single(...)` mock spawn，断言 worktree 目录建起、被 git 识别（`git worktree list` 含该路径）。

**Step 2：跑测试验证失败**
Run: `pytest tests/test_subagent_manager.py::test_worktree_created -v`
Expected: FAIL（`run_single` 未实现）。

**Step 3：实现 `_new_worktree` + `run_single` 骨架**（spawn/join 在 2.2/2.3 填，本步只把 worktree 建起 + registry 注册 `state=running`）。

**Step 4：跑测试验证通过**
Run: `pytest tests/test_subagent_manager.py::test_worktree_created -v`
Expected: PASS。

**Done when:** `git worktree list` 在临时 repo 含新建的 `.ga/worktrees/<uuid>`；`registry[wid].state == "running"`。

### Task 2.2：subprocess spawn `agentmain.py`（env 接线 + 独立进程组）`[anywhere]`

**Files:**
- Modify: `assets/subprocess_worker.py`（实现 `spawn_agentmain`）、`subagent_manager.py`（`run_single` 调它）

**关键签名（实现 §4.4）：**
```python
def spawn_agentmain(worktree, *, schema, tools_subset, model=None, timeout_s=1800):
    env = {
        **os.environ,
        "GA_TASK_MODE": "isolated",
        "GA_TASK_RESULT_SCHEMA": json.dumps(schema),
        "GA_TASK_WORKTREE": str(worktree),
        "GA_TASK_TOOLS": ",".join(sorted(set(tools_subset) | {"submit_result"})),
    }
    if model: env["GA_TASK_MODEL"] = str(model)
    cmd = [sys.executable, "agentmain.py", "--func", "-", "--nobg", "--nolog", "--no-user-tools"]
    kwargs = dict(cwd=str(worktree), env=env)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)
```
> `--func -` / `--nobg` 等旗标对齐 `ga_ultraplan.py:172-175` 既有调用；`description` 经 env/stdin 传（见 4.2 子模式）。`submit_result` 强制并入（决策 6）。

**Step 1：写失败测试** — `monkeypatch subprocess.Popen` 返回 fake proc，断言传给 Popen 的 `env` 含 4 个 `GA_TASK_*` 键、`cwd`==worktree、POSIX 下 `start_new_session=True`（或 Windows `creationflags` 含 CREATE_NEW_PROCESS_GROUP）。

**Step 2–4：** 同 TDD 循环。

**Done when:** 单测断言 Popen kwargs/env 正确；`kill_process_group` 仍 stub（3.3 实现）。

### Task 2.3：join 后读结果文件 + 双重 schema 校验 `[anywhere]`

**Files:**
- Modify: `subagent_manager.py`（`run_single` 的 join + 读 + 校验段，依赖 `validate` 见 4.x）

**关键逻辑（§4.6 双重校验）：**
```python
def run_single(self, desc, schema, base_ref=None, model=None, tools=None, timeout_s=DEFAULT_TIMEOUT_S):
    wt = self._new_worktree(base_ref)
    wid = os.path.basename(wt)
    self.registry[wid] = WorkerResult(uuid=wid, state="running", worktree_path=wt)
    proc = spawn_agentmain(wt, schema=schema, tools_subset=tools or [], model=model, timeout_s=timeout_s)
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_process_group(proc)   # 3.3
        self._retain(wid, wt, "timed_out"); return WorkerResult(wid, "timed_out", error="timeout", worktree_path=wt)
    rf = Path(wt) / RESULT_FILENAME
    if not rf.exists():
        self._retain(wid, wt, "failed"); return WorkerResult(wid, "failed", error="no result file", worktree_path=wt)
    try:
        obj = json.loads(rf.read_text(encoding="utf-8"))
    except Exception as e:
        self._retain(wid, wt, "failed"); return WorkerResult(wid, "failed", error=f"bad json: {e}", worktree_path=wt)
    err = validate(obj, schema)   # 第二道校验（防子进程绕过 submit_result 直写）
    if err:
        self._retain(wid, wt, "failed"); return WorkerResult(wid, "failed", error=f"schema: {err}", worktree_path=wt)
    return WorkerResult(wid, "succeeded", result=obj, worktree_path=wt)
```
> `validate` 在组 4 实现；本任务先 `from subagent_manager import validate` 并写一个临时占位返回 None 让单测过，组 4 替换为真实现。

**Step 1：写失败测试** — fake proc：写合规 result 文件 → `run_single` 返回 `state="succeeded"`、`result=={...}`。再写两个失败用例：不写文件→`failed`、写非法 JSON→`failed`。

**Step 2–4：** TDD。

**Done when:** 三用例（合规/无文件/坏 JSON）全绿；合规时 `registry[wid].state=="succeeded"`。

### Task 2.4：成功清理 / 失败保留 worktree `[anywhere]`

**Files:**
- Modify: `subagent_manager.py`（加 `_cleanup_worktree` + `_retain`）

**关键逻辑：**
```python
def _cleanup_worktree(self, wt):
    subprocess.run(["git", "worktree", "remove", "--force", wt], cwd=str(self.root), capture_output=True)
    shutil.rmtree(wt, ignore_errors=True)

def _retain(self, wid, wt, reason):
    self.registry[wid] = self.registry.get(wid) or WorkerResult(wid, "retained", worktree_path=wt)
    self.registry[wid].state = reason if reason in ("failed","timed_out") else "retained"
    self.retained.append((wid, wt, reason, time.time()))
    self._enforce_retained_cap()   # 3.4
```
> `run_single` 成功分支末尾调 `_cleanup_worktree(wt)` 并置 `state="cleaned"`；失败/超时调 `_retain`（已在 2.3 内联，本任务提取为方法）。

**Step 1：写失败测试** — 合规 result → worktree 目录不存在、`git worktree list` 不含该路径、registry `state=="cleaned"`。失败 → 目录保留、`retained` 含该 (wid,path,reason)。

**Step 2–4：** TDD。

**Done when:** 成功用例 `not os.path.exists(wt)`；失败用例 `os.path.exists(wt)` 且 `retained` 含记录。

---

## 组 3：批并发 / 超时 / 进程组 / GC

### Task 3.1：`run_batch`（ThreadPoolExecutor 扇出 + 保序）`[anywhere]`

**Files:**
- Modify: `subagent_manager.py`

**关键逻辑（决策 4/5 — 单 StepOutcome 在 do_task 拼，本任务只返回有序列表）：**
```python
def run_batch(self, tasks, max_concurrent=None):
    n = len(tasks)
    workers = max_concurrent or self.max_concurrent
    results = [None] * n
    with ThreadPoolExecutor(max_workers=min(workers, n) or 1) as ex:
        futs = {ex.submit(self.run_single, *t): i for i, t in enumerate(tasks)}
        for f in as_completed(futs):
            i = futs[f]; results[i] = f.result()   # 按 index 回填保序
    return results   # 有序 list，index=输入序
```

**Step 1：写失败测试** — 4 任务、`max_concurrent=2`、mock `run_single` 返回含 index 的假结果 → 断言返回 list 长度 4、`results[i].result == i`（保序）、并发上限 ≤2（用共享计数器断言峰值）。

**Step 2–4：** TDD。

**Done when:** 保序断言通过；并发峰值 ≤ max_concurrent。

### Task 3.2：`max_concurrent` env 读取 `[anywhere]`

**Files:**
- Modify: `subagent_manager.py`（`__init__` 已读 env，见 2.1）

**Step 1：写测试** — `monkeypatch.setenv("GA_SUBAGENT_MAX_CONCURRENT","8")` → `SubagentManager(root).max_concurrent == 8`；未设 → `==4`（决策 7）。

**Done when:** 两断言通过（已基本在 2.1 落地，补测试锁死）。

### Task 3.3：超时进程组 kill（跨平台）`[anywhere]`

**Files:**
- Modify: `assets/subprocess_worker.py`（`kill_process_group`）、`subagent_manager.py`（已在 2.3 调用）

**关键逻辑（决策 8 / §4.4）：**
```python
def kill_process_group(proc):
    if proc.poll() is not None: return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError: pass
```

**Step 1：写失败测试** — POSIX：fake proc，`monkeypatch os.getpgid` + 捕获 `os.killpg` 调用，断言 SIGKILL。Windows：fake proc，`monkeypatch subprocess.run`，断言 `["taskkill","/T","/F","/PID",pid]`。

**Step 2–4：** TDD。

**Done when:** 两平台分支断言通过；`run_single` 超时分支标 `state="timed_out"`（已在 2.3 接线）。

### Task 3.4：`retained[]` 上限 GC `[anywhere]`

**Files:**
- Modify: `subagent_manager.py`（`_enforce_retained_cap`）

**关键逻辑：**
```python
def _enforce_retained_cap(self):
    while len(self.retained) > self.max_retained:
        wid, wt, reason, ts = self.retained.pop(0)   # 删最旧
        self._cleanup_worktree(wt)   # 超限的 retained 也清掉
```

**Step 1：写测试** — `max_retained=2`，造 3 个 retained → 第 1 个被清（`not exists`），list 长度 2。

**Done when:** cap 断言通过。

### Task 3.5：启动 GC（prune 孤儿 worktree）`[anywhere]`

**Files:**
- Modify: `subagent_manager.py`（`_startup_gc`，`__init__` 末尾调）

**关键逻辑（§4.1 / spec「启动清理孤儿」）：**
```python
def _startup_gc(self):
    if not self.wt_root.exists(): return
    subprocess.run(["git", "worktree", "prune"], cwd=str(self.root), capture_output=True)
    for d in self.wt_root.iterdir():
        if not d.is_dir(): continue
        # 无 pid 文件或 pid 不活 → 孤儿
        if self._is_orphan(d):
            shutil.rmtree(d, ignore_errors=True)

def _is_orphan(self, wt_dir):
    # 简化：若目录存在但 registry 无记录（启动时 registry 必空）即视为孤儿
    return True
```
> 注：启动时 `registry` 必空，故 `.ga/worktrees/*` 下所有目录皆孤儿，全清。`pid` 活性检测为 v1.5 增强（live steering 同期），v1 用「目录存在 + 无 pidfile」启发式即可；spec 只要求「无活进程占用即 prune」，本实现满足。

**Step 1：写测试** — 临时 repo 下预放 `.ga/worktrees/old-uuid/`（含一个文件）→ `SubagentManager(tmp)` 构造后该目录被删、`git worktree prune` 被调用。

**Done when:** 孤儿目录被清；`git worktree list` 不含孤儿路径。

---

## 组 4：子模式 schema 强制（submit_result）

### Task 4.1：`submit_result` 工具条目入 schema `[anywhere]`

**Files:**
- Modify: `assets/tools_schema.json`、`assets/tools_schema_cn.json`（两份同步）

**Step 1：写条目**（参数即返回对象，schema 在运行期由 `GA_TASK_RESULT_SCHEMA` 覆盖）：
```json
{
  "type": "function",
  "function": {
    "name": "submit_result",
    "description": "子 agent 终末工具：参数即返回对象。必须按 GA_TASK_RESULT_SCHEMA 校验通过；不合规会返回错误让你重试。合规即结束本轮子任务。",
    "parameters": {
      "type": "object",
      "properties": {
        "result": { "type": "object", "description": "返回对象（shape 由 task 调用者 result_schema 定，运行期注入）" }
      },
      "required": ["result"],
      "additionalProperties": false
    }
  }
}
```
> 该条目仅子模式注入（agentmain 子模式过滤工具集时强制并入，决策 6）；主循环 agent 看不到（5.3 gate 过滤 + submit_result 不在主 schema 暴露路径）。放第 14 位（task 是第 13，见 5.1）。

**Step 2：校验 JSON 合法**
Run: `python -c "import json; json.load(open('assets/tools_schema.json',encoding='utf-8')); json.load(open('assets/tools_schema_cn.json',encoding='utf-8')); print('ok')"`
Expected: `ok`。

**Done when:** 两文件含 `submit_result` 条目、JSON 合法、共 14 项（task 入后总数）。

### Task 4.2：`agentmain.py` 子模式 env 接线 `[anywhere]`

**Files:**
- Modify: `agentmain.py`（在 `load_tool_schema()` 之后、`agent_runner_loop` 启动前加子模式分支）

**关键逻辑（§4.8）：**
```python
# 紧接 load_tool_schema() 之后
if os.environ.get("GA_TASK_MODE") == "isolated":
    _task_result_schema = json.loads(os.environ["GA_TASK_RESULT_SCHEMA"])
    _task_worktree = os.environ["GA_TASK_WORKTREE"]
    _allowed = set(filter(None, os.environ.get("GA_TASK_TOOLS", "").split(",")))
    _allowed.add("submit_result")   # 强制并入（决策 6）
    TOOLS_SCHEMA = [t for t in TOOLS_SCHEMA if t.get("function", {}).get("name") in _allowed]
    # 确保 submit_result 在（若原 schema 未含则从备份补）
    _names = {t["function"]["name"] for t in TOOLS_SCHEMA}
    if "submit_result" not in _names:
        TOOLS_SCHEMA.append(_submit_result_entry())   # 从 json 文件读或硬编码
    _task_model_override = os.environ.get("GA_TASK_MODEL")
    # 系统提示追加：终末必须调 submit_result，参数即返回对象，须合 result_schema
```
> 系统提示片段示例：`"你是隔离子 agent。你的终末动作 MUST 是调用 submit_result，参数 result 即你要返回的对象，须符合给定 schema。不调 submit_result 视为失败。"`

**Step 1：写失败测试** — 设置 4 个 env → 调一个提取出的纯函数 `_apply_task_mode(TOOLS_SCHEMA, env)`（把副作用提为纯函数便于单测）→ 断言返回的工具集 ⊆ allowed 且含 `submit_result`。

**Step 2–4：** TDD（提取纯函数 `_apply_task_mode` 便于不启 agentmain 主进程即可单测）。

**Done when:** 单测断言工具子集 = allowed ∪ submit_result；`agentmain.py` 仍可正常 `python -c "import ast; ast.parse(open('agentmain.py').read())"`。

### Task 4.3：`submit_result` 执行器（校验 + 写文件 + should_exit）`[anywhere]`

**Files:**
- Modify: `ga.py`（加 `do_submit_result` 方法到 `GenericAgentHandler`）

**关键逻辑（§4.3 + 决策 2）：**
```python
def do_submit_result(self, args, response):
    """子模式终末工具：校验 result vs GA_TASK_RESULT_SCHEMA，合规即写文件 + should_exit 硬终止子 loop。"""
    schema = json.loads(os.environ["GA_TASK_RESULT_SCHEMA"])
    obj = args.get("result")
    err = validate(obj, schema)   # 手写校验器（组 4 的 validate，实现在 subagent_manager）
    if err:
        yield f"[submit_result] schema validation failed: {err}\n"
        return StepOutcome({"status": "error", "reason": err}, next_prompt=f"submit_result 校验失败: {err}。请修正后重新调用。")
    wt = Path(os.environ["GA_TASK_WORKTREE"])
    (wt / ".ga_task_result.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    self.should_exit = True   # 复用既有 do_exit/should_exit 通路（agent_loop.py:91 硬终止）
    yield "[submit_result] result submitted, exiting child loop.\n"
    return StepOutcome({"status": "ok", "msg": "result submitted"}, next_prompt="", should_exit=True)
```
> `validate` 从 `subagent_manager` import（组 4 同模块共享）。`should_exit=True` → `agent_loop.py:91-92` 立即 break，硬保证「终末=结果」「无散文」（决策 2）。非合规 → 不 exit，返回错误 next_prompt 让子 agent 重试，上限 `max_turns`（决策 3）。

**Step 1：写失败测试** — 合规 result → 写文件、`should_exit==True`。非合规（缺 required key）→ 不写文件、`should_exit==False`、error 含 `missing required`。

**Step 2–4：** TDD（`validate` 真实现见下）。

**Done when:** 两用例通过；合规时 `.ga_task_result.json` 存在且内容 == args["result"]。

### Task 4.4：手写 `validate` 最小校验器 `[anywhere]`

**Files:**
- Modify: `subagent_manager.py`（实现 `validate`）

**关键逻辑（§4.5）：**
```python
def validate(obj, schema):
    if not isinstance(schema, dict): return "schema not object"
    t = schema.get("type")
    if t == "object":
        if not isinstance(obj, dict): return "not object"
        for k in schema.get("required", []):
            if k not in obj: return f"missing required '{k}'"
        props = schema.get("properties", {})
        for k, v in obj.items():
            if k in props:
                e = validate(v, props[k])
                if e: return f".{k}: {e}"
            elif schema.get("additionalProperties") is False:
                return f"extra key '{k}'"
        if "enum" in schema and obj not in schema["enum"]: return "not in enum"
        return None
    if t == "array":
        if not isinstance(obj, list): return "not array"
        it = schema.get("items")
        if it:
            for i, x in enumerate(obj):
                e = validate(x, it)
                if e: return f"[{i}]: {e}"
        if "enum" in schema and obj not in schema["enum"]: return "not in enum"
        return None
    if t == "string":  ok = isinstance(obj, str)
    elif t == "number": ok = isinstance(obj, (int, float)) and not isinstance(obj, bool)
    elif t == "boolean": ok = isinstance(obj, bool)
    elif t == "integer": ok = isinstance(obj, int) and not isinstance(obj, bool)
    elif t is None: ok = True   # 无 type 约束
    else: ok = True
    if not ok: return f"not {t}"
    if "enum" in schema and obj not in schema["enum"]: return "not in enum"
    return None
```

**Step 1：写失败测试**（合规/非合规全覆盖，单测文件 `tests/test_submit_result.py` 或合并入 `test_subagent_manager.py`）：
- object required 缺失 → `missing required 'x'`
- additionalProperties:false + 多余键 → `extra key 'y'`
- array items 递归校验
- enum 非法值
- 嵌套 object `.a.b: not string`
- bool 不被当 number

**Step 2–4：** TDD。

**Done when:** 6+ 用例全绿；`validate` 返回 None（合规）或 str（错误）。

---

## 组 5：task 工具 + do_task 接线

### Task 5.1：`task` 工具条目入 schema `[anywhere]`

**Files:**
- Modify: `assets/tools_schema.json`、`assets/tools_schema_cn.json`（两份同步）

**Step 1：写条目（第 13 项，收单或列表）：**
```json
{
  "type": "function",
  "function": {
    "name": "task",
    "description": "扇出隔离子 agent（各自独立 git worktree）。传入单任务或任务列表（批模式）。子 agent 返回经 result_schema 校验的结构化对象，父直读无散文。需 GA_SUBAGENT_ENABLED=1。",
    "parameters": {
      "type": "object",
      "properties": {
        "description": { "type": "string", "description": "任务描述（传给子 agent 的完整上下文）" },
        "result_schema": { "type": "object", "description": "期望返回对象的 JSON schema（手写校验器支持 type/required/properties/items/enum/additionalProperties:false）" },
        "base_ref": { "type": "string", "description": "worktree 基点 git ref（默认当前 HEAD）" },
        "model": { "type": "string", "description": "子 agent 用的 model id（可选覆盖）" },
        "tools": { "type": "array", "items": {"type":"string"}, "description": "子 agent 工具子集白名单（∪ 强制 submit_result）" },
        "timeout_s": { "type": "integer", "description": "超时秒（默认 1800）", "default": 1800 }
      },
      "required": ["description", "result_schema"]
    }
  }
}
```
> 列表模式：LLM 也可传 `tasks: [{...}, ...]`，do_task 统一成 list（见 5.2）。schema 用 `oneOf` 表达单/列表会超出校验器支持范围，故不约束 single-vs-list，由 do_task 容错解析。

**Step 2：校验 JSON**
Run: `python -c "import json; d=json.load(open('assets/tools_schema.json',encoding='utf-8')); print([t['function']['name'] for t in d])"`
Expected: 含 `task`（第 13）。

**Done when:** 两文件含 `task`、JSON 合法、共 14 项（task + submit_result）。

### Task 5.2：`ga.py` `do_task`（gen-style，镜像 do_skill_manage）`[anywhere]`

**Files:**
- Modify: `ga.py`（加 `do_task` 到 `GenericAgentHandler`；`__init__` 实例化 `self._subagent_mgr`）

**关键逻辑（§4.2 + 决策 4）：**
```python
# ga.py 顶部 import
from subagent_manager import SubagentManager, WorkerResult

# __init__ 末尾（ga.py:268 段）
def __init__(self, parent, last_history=None, cwd='./temp'):
    ...  # 既有字段
    self._subagent_mgr = SubagentManager(root=os.path.abspath(os.path.join(cwd, '..')) or cwd)
    # 启动 GC 在 SubagentManager.__init__ 内已跑

def do_task(self, args, response):
    '''一等公民子 agent 扇出。单任务返回对象；列表返回有序结果列表（1 个 StepOutcome）。需 GA_SUBAGENT_ENABLED。'''
    if os.environ.get("GA_SUBAGENT_ENABLED") not in ("1", "true", "True"):
        yield "[task] disabled (GA_SUBAGENT_ENABLED unset)\n"
        return StepOutcome({"status": "disabled", "msg": "GA_SUBAGENT_ENABLED not set"}, next_prompt="\n")
    single = "tasks" not in args
    tasks = [args] if single else args["tasks"]
    yield f"[task] dispatching {len(tasks)} subagent(s)\n"
    results = self._subagent_mgr.run_batch([
        (t["description"], t["result_schema"], t.get("base_ref"), t.get("model"),
         t.get("tools"), t.get("timeout_s", 1800))
        for t in tasks
    ])
    ok = [r.result for r in results if r.state == "succeeded"]
    errs = [{"index": i, "reason": r.error, "state": r.state} for i, r in enumerate(results) if r.state != "succeeded"]
    if single:
        r0 = results[0]
        yield f"[task] done: {r0.state}\n"
        return StepOutcome(r0.result if r0.state == "succeeded" else None,
                           error=(r0.error if r0.state != "succeeded" else None), next_prompt="\n")
    yield f"[task] batch done: {len(ok)}/{len(results)} succeeded\n"
    return StepOutcome(result=ok, error=errs or None, next_prompt="\n")
```
> 单 tool_call 批模式产 **1 个 StepOutcome**（决策 4）；列表 `result` 有序（决策 5）；部分失败 `error` 槽列失败 index+reason，成功项仍在 `result`（父容错消费）。`StepOutcome` 不含 `error` 字段时用 `data` 封装 `{result, errors}`——核对 `StepOutcome` dataclass（`agent_loop.py:7` 仅 `data/next_prompt/should_exit`），故 do_task 把 `(result, errs)` 合进 `data`：`StepOutcome({"result": ok, "errors": errs}, next_prompt="\n")`。实现时按 dataclass 实际字段调整。

**Step 1：写失败测试** — `monkeypatch GA_SUBAGENT_ENABLED=1` + mock `SubagentManager.run_batch` 返回 2 个 succeeded → `do_task` 产 1 个 StepOutcome、`data` 含 2 元素有序列表。单任务分支：返回单对象。

**Step 2–4：** TDD（`SubagentManager` 与 `run_batch` mock）。

**Done when:** 单/批两分支单测通过；批模式恰好 1 个 StepOutcome、保序。

### Task 5.3：feature gate `GA_SUBAGENT_ENABLED` 过滤 `task` `[anywhere]`

**Files:**
- Modify: `agentmain.py`（`load_tool_schema` 末尾按 gate 过滤 `task`）或 `ga.py`（do_task 内已二次校验，见 5.2）

**关键逻辑（§4.7 纵深防御，两道）：**
```python
# agentmain.py load_tool_schema() 末尾
if os.environ.get("GA_SUBAGENT_ENABLED") not in ("1", "true", "True"):
    TOOLS_SCHEMA = [t for t in TOOLS_SCHEMA if t.get("function", {}).get("name") != "task"]
```
> 第二道在 `do_task` 内（5.2 已落地）。gate off → 主循环 agent 工具表面无 `task`，legacy 完全不变（spec「gate 关闭」Scenario）。

**Step 1：写测试** — gate 未设 → `task` 不在 TOOLS_SCHEMA；`GA_SUBAGENT_ENABLED=1` → 在。

**Step 2–4：** TDD（提取纯函数 `_apply_gate(TOOLS_SCHEMA, env)` 便于单测）。

**Done when:** 两断言通过；gate off 时 `[t['function']['name'] for t in TOOLS_SCHEMA]` 不含 `task`。

---

## 组 6：共享 spawn 管线提炼（可选复用）

### Task 6.1：从 `ga_ultraplan` 提炼共享逻辑到 `subprocess_worker.py` `[anywhere]`

**Files:**
- Modify: `assets/subprocess_worker.py`（已在 2.2 实现 `spawn_agentmain`/`kill_process_group`）、`assets/ga_ultraplan.py`（**可选**迁移 `_subagent` 复用）

**关键逻辑：**
- `subprocess_worker.spawn_agentmain` / `kill_process_group` 已在 2.2/3.3 实现并被 `task` 复用。
- 本任务仅评估 `ga_ultraplan._subagent`（`ga_ultraplan.py:165-178`）是否迁移为调 `spawn_agentmain`。**非强制**——ultraplan 现有 `subprocess.run`（同步、stdout 文件、无 worktree）与 `task` 语义不同（无 worktree/result_schema），强行迁移会破 legacy（违反 Non-Goal）。

**Step 1：核对兼容性** — 读 `ga_ultraplan._subagent` 现有调用方（`_run`/`parallel`），确认迁移会否改变 stdout 文件契约。
**Step 2：决策** — 若迁移会破 legacy → 标 `# follow-up: migrate ga_ultraplan to subprocess_worker (v1.5)` 注释，不迁移。若可安全迁移则迁移 + 跑 ultraplan 既有测试。
**Step 3：文档** — design doc §9 已列「ultraplan 迁移复用为可选 follow-up，非强制」，本任务对齐。

**Done when:** 决策记录在 `subprocess_worker.py` docstring（「ultraplan 迁移 = follow-up，v1 不强制」）；`task` 已复用 `spawn_agentmain`（前置依赖 2.2/3.3 完成）。

---

## 组 7：测试

### Task 7.1：单测 `subagent_manager` `[anywhere]`

**Files:**
- Create: `tests/test_subagent_manager.py`

**覆盖（已分散在 2.1–3.5 的 TDD 步骤，本任务收口补全边界）：**
- `run_single` happy path（mock 子进程写合规 result）
- `run_batch` 并发 + 保序 + max_concurrent 上限
- worktree 建删（成功清理 / 失败保留）
- `retained` 上限 GC
- `_startup_gc` 清孤儿
- 超时 → `timed_out` + 保留（mock `TimeoutExpired`）
- 双重校验第二道（result 文件 schema 不符 → `failed`）

**Step 1：补全缺失用例** — 逐一跑 `pytest tests/test_subagent_manager.py -v`，补红到绿。

**Done when:** `pytest tests/test_subagent_manager.py -v` 全绿；`ruff check tests/test_subagent_manager.py` 0 违规。

### Task 7.2：单测 `submit_result` 校验器 `[anywhere]`

**Files:**
- Create: `tests/test_submit_result.py`

**覆盖（4.4 的 validate + 4.3 的 do_submit_result 执行器）：**
- validate：object/array/scalar 合规与非合规、required 缺失、additionalProperties:false、enum、嵌套
- do_submit_result：合规 → 写文件 + `should_exit=True`；非合规 → 不写 + `should_exit=False` + error

**Done when:** `pytest tests/test_submit_result.py -v` 全绿；`ruff check tests/test_submit_result.py` 0 违规。

### Task 7.3：集成 T1 — 单任务端到端 `[env-blocked]`

> ⚠️ 需 deps-complete env（Windows `.venv` via `uv pip install -e ".[ui]"`）。Linux 沙箱不可执行（`import ga.py` 需 pywebview）。

**Files:**
- Create: `tests/test_subagent_integration.py`（`@pytest.mark.integration`，gate=on）

**T1 场景：** `GA_SUBAGENT_ENABLED=1` + monkeypatch 子 agent 产合规 result（fake agentmain 或预置 `.ga_task_result.json`）→ 端到端 `do_task` 单任务 → worktree 建起→清理→父直读对象。

**Step 1：写测试** — 用 fake agentmain 脚本（写合规 result 文件后 exit 0）替代真 agentmain（避免依赖 LLM）。
**Step 2：跑** `pytest tests/test_subagent_integration.py::test_T1_single -v`
Expected: PASS（worktree 建起→清理→`result == 预期对象`）。

**Done when:** T1 绿；worktree 被清理（`not exists`）。

### Task 7.4：集成 T2 — 批模式并行 + max_concurrent `[env-blocked]`

**T2 场景：** N=6 任务、`max_concurrent=4` → 4 并行 + 2 排队、返回 6 结果保序。

**Step 1–2：** 写 + 跑 `pytest tests/test_subagent_integration.py::test_T2_batch -v`。

**Done when:** 返回 list 长度 6、保序、并发峰值 ≤4、全部 worktree 清理。

### Task 7.5：集成 T3 — schema 强制（failed + 保留）`[env-blocked]`

**T3 场景：** 子 agent 不调 `submit_result`（或调非合规）→ max_turns 耗尽 → `failed` + worktree 保留。

**Done when:** `state=="failed"`、worktree 保留（`exists`）、`retained` 含记录。

### Task 7.6：集成 T4 — 兄弟隔离 `[env-blocked]`

**T4 场景：** 两 worker 改同名文件 `foo.txt`（内容不同）→ 互不干扰、无孤儿编辑、各自 worktree 独立。

**Done when:** 两 worktree 的 `foo.txt` 内容各自正确；merge 无冲突（worktree 隔离保证）。

### Task 7.7：集成 T5 — 超时 + 进程组 kill `[env-blocked]`

**T5 场景：** `timeout_s=2` + fake agentmain sleep 10 → 进程组 kill + `timed_out` + 保留。

**Done when:** `state=="timed_out"`、进程被 kill（`proc.poll() is not None`）、worktree 保留。Windows 分支用 `taskkill /T`、POSIX 用 `killpg`（3.3 已单测，此处验端到端）。

### Task 7.8：集成 T6 — legacy 不破 `[env-blocked]`

**T6 场景：**
- gate off → `task` 不在 TOOLS_SCHEMA（spec「gate 关闭」Scenario）。
- gate on 后调 conductor / ultraplan / btw → 行为不变（spec「legacy 不受影响」Scenario）。

**Done when:** gate off 时 `[t['function']['name'] for t in TOOLS_SCHEMA]` 不含 `task`；conductor/ultraplan/btw 既有断言（若有）不变。

### Task 7.9：deps-complete 全套 ruff/pytest `[env-blocked]`

> ⚠️ 需 Windows `.venv` via `uv pip install -e ".[ui]"`。

**Step 1：新代码 lint**
Run: `ruff check plugins/ assets/subprocess_worker.py tests/test_subagent_manager.py tests/test_submit_result.py subagent_manager.py`
Expected: 0 违规。

**Step 2：单测 + 集成全跑**
Run: `pytest tests/test_subagent_manager.py tests/test_submit_result.py tests/test_subagent_integration.py -v`
Expected: 全绿。

**Step 3：全量回归（不引入新失败）**
Run: `pytest tests/`
Expected: 无新失败（baseline flakiness 不计；与本变更前对比）。

**Done when:** 三命令均达期望。

---

## 组 8：文档

### Task 8.1：`AGENTS.md` 登记 task/submit_result + gate `[anywhere]`

**Files:**
- Modify: `AGENTS.md`

**Step 1：** 在 `AGENTS.md` 适当段（工具/gate 段）登记：
- `task` 工具（主循环 agent，第 13 项，扇出隔离子 agent）
- `submit_result` 工具（子模式注入，不暴露给主循环）
- feature gate `GA_SUBAGENT_ENABLED`（默认 off）、`GA_SUBAGENT_MAX_CONCURRENT`（默认 4）
- 子模式 env 契约：`GA_TASK_MODE=isolated` / `GA_TASK_RESULT_SCHEMA=<json>` / `GA_TASK_TOOLS=<csv>` / `GA_TASK_WORKTREE=<path>` / `GA_TASK_MODEL=<id>`

**Done when:** `AGENTS.md` 含上述条目；`ruff`/markdown lint 无新违规。

### Task 8.2：标 conductor/ultraplan/btw 为 legacy `[anywhere]`

**Files:**
- Modify: `frontends/conductor.py`、`assets/ga_ultraplan.py`、`frontends/btw_cmd.py`（顶部 docstring/注释加 `# LEGACY:` 标记）；可选 `docs/` 加一句

**Step 1：** 三文件顶部加注释：`# LEGACY: 不在本变更范围；保留供兼容，一等公民子 agent 见 task 工具 / subagent_manager.py。`
**Done when:** 三文件含 LEGACY 标记；行为不变（仅注释）。

### Task 8.3：更新 HANDOFF / design 摘要 `[anywhere]`

**Files:**
- Modify: `HANDOFF.md`（或新增 design 摘要段）

**Step 1：** 加段：链接 design doc、spec、本计划；列 9 已确认决策一句话摘要；列 env 约束（T1–T6/7.9 需 Windows deps-complete）。
**Done when:** `HANDOFF.md` 含摘要 + 链接。

---

## 任务依赖图（执行顺序提示）

```
1.1 → 1.2 → 1.3（骨架）
2.1 → 2.2 → 2.3 → 2.4（run_single 闭环，依赖 validate stub）
3.1 → 3.2 → 3.3 → 3.4 → 3.5（并发/超时/GC）
4.4（validate 真实现）→ 4.3（do_submit_result）→ 4.1（schema 条目）→ 4.2（agentmain 子模式）
5.1（task schema）→ 5.2（do_task）→ 5.3（gate）
6.1（可选提炼，依赖 2.2/3.3）
7.1/7.2（单测，[anywhere]）可随 2.x/4.x 并行
7.3–7.8（集成，[env-blocked]）须 5.x 完成后 + Windows env
7.9（全套，[env-blocked]）最后
8.x（文档，[anywhere]）随时
```

> **执行者注意**：[anywhere] 任务可随时做；[env-blocked] 任务（7.3–7.9）必须切 Windows `.venv`（`uv pip install -e ".[ui]"`）。若当前在 Linux 沙箱，先做完全部 [anywhere] 任务与 7.1/7.2 单测，[env-blocked] 留到 Windows 会话。

## 全部验证（完成后跑）

```bash
# [anywhere] 新代码 lint
ruff check plugins/ assets/subprocess_worker.py tests/test_subagent_manager.py tests/test_submit_result.py subagent_manager.py

# [anywhere] 单测
pytest tests/test_subagent_manager.py tests/test_submit_result.py -v

# [env-blocked] 集成 + 全量回归（Windows .venv）
pytest tests/test_subagent_integration.py -v
pytest tests/   # 不引入新失败
```
