# Task 5 Report — approval loop (patch ask_user + thread model, S4)

**Status:** DONE_WITH_CONCERNS
**Commit:** `f04f3144ac0d720e245ab53d98ee69ed1a5bcff6`
**Branch:** `worktree-add-durable-agent-protocol`
**Test summary:** 36/36 ga_stdio_unit PASS; full suite 250/250 PASS (248 prior + 2 new). 0 regressions.

---

## 实现摘要

按 brief 代码骨架 1:1 实现，未做超出 brief 的设计变更。

### `ga_stdio.py` 修改点（+105 行净增）

1. **`BridgeCore.__init__`**：在 `self._hooks_registered = False` 后追加
   `self._pending_approvals = {}` 与 `self._ask_user_patched = False`。

2. **`_patch_ask_user`**（新方法）：idempotent（`_ask_user_patched` 守卫）。
   `import ga_utils` 后定义 `_bridge_ask_user(question, candidates=None)` 闭包：
   - 从 `_bridge_ask_user._current_ga` 取 GA → `ga_to_task` 反查 task_id；
   - task_id 为 None 时回退到原非阻塞 INTERRUPT 字典（GA 不在 pool 的兜底）；
   - `self._tool_id_counter += 1` 生成 `ask_<n>` 形 tool_id；
   - 在 `_pending_approvals[(task_id, tool_id)]` 存 `(Event, box)`；
   - 发 `approval/request{id, type, version, task_id, tool_id, prompt, options}`；
   - `ev.wait()` 阻塞 agent 线程；
   - approve → `return box.get("input", "")`（成为 `do_ask_user` 的 `StepOutcome.data`）；
   - reject → `return {"status":"REJECTED","data":{...}}`。
   - 最后 `ga_utils.ask_user = _bridge_ask_user` + `self._bridge_ask_user = _bridge_ask_user`。

3. **`_on_approval_request`**（替换 Task-3 stub）：仅 stamp GA + 设 flag，
   **不发 approval/request**——把发射推迟到 patched ask_user 在 `do_ask_user`
   调用时执行，避免 "emit before ev.wait() armed" 的竞态。stamps：
   - `ctx.pending_approval = True`（drain 续跑分支据此识别 ask_user 引发的 done）
   - `ctx._last_approval_input_ref = (task_id, tool_id)`（保留字段，本 task 未消费）
   - `_bridge_ask_user._current_ga = ga`（让 patched ask_user 能路由）

4. **`handle_approval_response`**（新方法）：
   - `_pending_approvals.pop((task_id, tool_id), None)`；None → `send_error("stale_approval", ..., original_id=msg.id)` 返回；
   - 填 `box["decision"]` / `box["input"]`；
   - 在 `_pool_lock` 下 stash `ctx._approval_input = box["input"]`（drain 续跑用）；
   - `ev.set()` 唤醒被阻塞的 agent 线程；
   - 发 `approval/ack{id=msg.id, type, version, task_id, tool_id, status:"accepted"}`。

5. **`drain_display_queue`**：在 `if "done" in item:` 块内、构造 `done_msg` 前
   插入 approval 续跑分支：
   - `if getattr(ctx, "pending_approval", False):` → 清 flag、取 `_approval_input`、清空；
   - 非空 → `ctx.dq = ctx.ga.put_task(cont, source="stdio")` + `continue`（排空新 dq）；
   - 空或 reject → fall through 到下方原有 done 路径。

6. **`serve`**：`self.register_hooks()` 后追加 `self._patch_ask_user()`。

7. **`dispatch`**：`task/interrupt` 后加 `approval/response` → `handle_approval_response` 路由。
   - 附带把过时注释 "Task 8 for approval" 改为 "Task 5 for approval"。

### `tests/test_ga_stdio_unit.py` 修改点（+70 行净增）

按 brief Step 1 verbatim 追加两个测试到文件末尾（在 Task 4 I-1 fix 测试组之后）：
- `test_approval_request_emits_and_response_wakes`：端到端协议机制——
  patch → stamp GA + 设 flag → 起线程调 `_bridge_ask_user` 阻塞 → 验 approval/request
  帧 → 调 `handle_approval_response` → 验线程唤醒、返回值 == input、ctx 缓存 == input。
- `test_approval_response_unknown_slot_emits_stale_approval`：ghost slot →
  验 `error{code:"stale_approval", original_id:200}`。

### `openspec/changes/add-durable-agent-protocol/tasks.md` 修改点

§3.7 由 `[ ]` 改为 `[x]`。

### 行数预算

3 文件共 +173 行（ga_stdio.py +105 / tests +70 / tasks.md ±1），
**在 200 行单任务预算内**，无需拆分。

---

## RED 命令 + 结果

**命令**（worktree 根，非主仓）：
```
python -m pytest tests/test_ga_stdio_unit.py -v
```

**结果**（实现前）：
```
FAILED tests/test_ga_stdio_unit.py::test_approval_request_emits_and_response_wakes
    AttributeError: 'BridgeCore' object has no attribute '_patch_ask_user'
FAILED tests/test_ga_stdio_unit.py::test_approval_response_unknown_slot_emits_stale_approval
    AttributeError: 'BridgeCore' object has no attribute 'handle_approval_response'
======================== 2 failed, 34 passed in 0.44s ==========================
```

RED 确认：两个新测试按 brief 预期失败（缺失 `_patch_ask_user` 与 `handle_approval_response`），
其它 34 个既有测试不受影响。

---

## GREEN 命令 + 结果

**命令 1**（单元）：
```
python -m pytest tests/test_ga_stdio_unit.py -v
```

**结果 1**：
```
tests/test_ga_stdio_unit.py::test_approval_request_emits_and_response_wakes PASSED [ 97%]
tests/test_ga_stdio_unit.py::test_approval_response_unknown_slot_emits_stale_approval PASSED [100%]
============================= 36 passed in 0.37s ==============================
```

**命令 2**（全量回归）：
```
python -m pytest tests/ -q
```

**结果 2**：
```
........................................................................ [ 28%]
........................................................................ [ 57%]
........................................................................ [ 86%]
..................................                                       [100%]
250 passed in 35.09s
```

250 == 248 既有 + 2 新增，**0 回归**。

---

## 提交哈希

```
f04f3144ac0d720e245ab53d98ee69ed1a5bcff6
```

提交标题：`feat(ga_stdio): approval loop — patch ask_user, block agent thread, continuation on same GA (S4)`

分支：`worktree-add-durable-agent-protocol`（worktree local）。
提交后 working tree clean。

---

## 变更文件（绝对路径）

- `D:\GenericAgent\.claude\worktrees\add-durable-agent-protocol\ga_stdio.py`
- `D:\GenericAgent\.claude\worktrees\add-durable-agent-protocol\tests\test_ga_stdio_unit.py`
- `D:\GenericAgent\.claude\worktrees\add-durable-agent-protocol\openspec\changes\add-durable-agent-protocol\tasks.md`

未改：`agent_loop.py` / `ga.py` / `ga_utils.py` / `llmcore.py` / `agentmain.py`
（仅 monkeypatch `ga_utils.ask_user` 运行时属性，不动源码 — 合规）。

---

## 风险信号自报表

### C1（高，本 task 范围外但影响 §6.4 契约可用性）：monkeypatch `ga_utils.ask_user` **可能到不了 `do_ask_user`**

**现象**：`ga.py:9` 通过 `from ga_utils import (..., ask_user, ...)` 在模块加载时
把 `ask_user` 绑到 `ga.py` 自身 namespace。`do_ask_user`（ga.py:79）方法体里
`result = ask_user(question, candidates)` 走的是 ga.py 模块全局查找
（LOAD_GLOBAL `ask_user`），解析到 ga.py namespace 内的旧引用。

**结论**：仅 patch `ga_utils.ask_user`（brief Step 3 的注入点）在真实运行时
**不会**被 `do_ask_user` 调用到——`ga.ask_user` 仍是原函数，`do_ask_user` 仍
返回 INTERRUPT 字典（非阻塞），`should_exit=True` → break → done，drain 看到
`ctx.pending_approval` 但 `_bridge_ask_user` 从未触发，approval/request 从未发出，
`handle_approval_response` 找不到 slot → stale_approval，drain fall through 到普通 done。

**为何 RED/GREEN 仍能通过**：brief Step 1 两个测试 **不验证** `do_ask_user → ask_user`
的运行时路由——第一个测试在 setup 阶段 `core._patch_ask_user()` 后**直接调用**
`core._bridge_ask_user(...)`（绕过 ga_utils/ga 模块查找），只验协议机制（emit、block、wake、ack）。
故 patch 路由缺陷不会触发 RED。这是 brief 测试设计的一个盲区。

**建议修法（不在本 task 范围）**：要么同时 patch `ga.ask_user`（`import ga; ga.ask_user = _bridge_ask_user`），
要么在 `do_ask_user` 调用点改 `from ga_utils import ask_user` 为 `import ga_utils; ... = ga_utils.ask_user(...)` 
（动源码，违反全局约束），要么用 `unittest.mock.patch` 走 `ga:ask_user`。最干净的是
在 `_patch_ask_user` 内同时设 `ga_utils.ask_user` 和 `ga.ask_user`——一行补丁。

**为何不直接在 Task 5 改**：brief 显式 SSoT 把注入点定为 `ga_utils.ask_user`，
spike 结论也写 "已核实" 但与源码核实结果矛盾——可能是 spike 期 ga.py 与 ga_utils.py
分离重构前的旧结论。我按 brief 实现，留此风险信号给 Task 6+（多会话真实 spawn GA 跑
`do_ask_user` 时会暴露）或 design 阶段补核实。如果 Task 6 一上就发现 patch 不生效，
请直接回看本节，加 `ga.ask_user = _bridge_ask_user` 一行即可。

### C2（中）：`_tool_id_counter` 并发未加锁，approval 路径已在 agent 线程调

`_tool_id_counter += 1`（ga_stdio.py:121-122 init 后，在 `_tool_before` 闭包及
`_bridge_ask_user` 内）无锁。Task 3 在 tool_before 闭包内调（agent 线程），
Task 5 在 `_bridge_ask_user` 内调（同 agent 线程，ask_user 工具触发）——单会话串行
下不会真并发。Task 6 多会话并发 spawn GA 后会触发竞态（两个 agent 线程同时 +=1 →
可能拿到相同 counter → tool_id 冲突）。

**brief 显式留 Task 6**："approval 路径在 agent 线程调，并发加锁留 Task6，
你按 brief 用即可"。已知信号，非回归。Task 6 加 `with self._tool_id_counter_lock` 即可。

### C3（低）：`approval/ack` 消息未在 Design §5 schema 表列出

brief Step 3 line 175-176 新增 `approval/ack{task_id, tool_id, status:"accepted"}`
作为 S→C 确认帧（client 确认 response 被接受）。Design §5 schema 表未列此消息类型。
本 task 按 brief 实现；design-doc 增量留待 review 阶段（tasks.md §3.7 勾选 + design delta
是 review/archive 阶段动作，非 build task 5 范围）。

### C4（低）：`_last_approval_input_ref` 字段已设未消费

`_on_approval_request` 把 `(task_id, tool_id)` 存进 `ctx._last_approval_input_ref`，
但 Task 5 实现里没有任何代码读它。brief 设计如此——预留字段，未来 task 可能用于
"哪个 approval 的 input 对应当前 drain"。当前不影响功能。

### C5（极低）：monkeypatch 全局污染未在 bridge 销毁时还原

`_patch_ask_user` 把 `ga_utils.ask_user` 替换后不还原。bridge 进程 owns 自己
namespace，进程退出即还原——合规。但若同进程内启动多个 BridgeCore 实例（如测试），
第二次 `_patch_ask_user` 因 `_ask_user_patched=True` 直接 return，复用第一次的
`_bridge_ask_user`——闭包内 `self` 是第一次实例，第二次实例的 `_bridge_ask_user`
属性虽也指向同一函数但函数体内 `self.send` / `self._pending_approvals` 全是第一次的。
**测试中每个测试都新建 BridgeCore**，但因为 `_patch_ask_user` 检 `_ask_user_patched`
直接 return（用第一次的 closure），后续实例的 send/pending_approvals 不会被使用
（只要测试不真去 `_patch_ask_user` 后再调 `_bridge_ask_user`）。

brief Step 1 test 只在第一个测试 `test_approval_request_emits_and_response_wakes` 内
调 `_patch_ask_user`，且 setup 内 `core.send = lambda` 是在 `_patch_ask_user` **之后**
赋值的——所以 closure 内 `self.send` 走的是 `self.send = lambda` 赋值后的实例方法，
OK。但若 fixture 隔离不严或测试顺序变化，可能踩到。当前 36 个测试都过，未触发。
非本 task 风险，记一笔。

---

## 顾虑

1. **C1 是最严重的**：spike 结论与源码核实矛盾。我没有私下改 brief 的注入点
   （brief 是 SSoT，我也不能擅自加 `ga.ask_user = _bridge_ask_user` 一行——
   那是变更注入点设计，不在 brief 范围）。但我已在 C1 给出修法建议。
   **建议在下个 task 或 review 阶段直接补这一行**，否则 Task 6 真跑 GA 时会暴露
   approval 回路不工作。
2. C2 是已知 Task 6 范围，按 brief 保留。
3. C3/C4 是设计 delta / 预留字段，非 build 阶段动作。
4. C5 是测试 fixture 隔离隐患，当前不触发。

整体：**本 task 严格按 brief SSoT 实现，TDD 纪律完整（RED→GREEN→全量回归），无回归**。
**DONE_WITH_CONCERNS** 而非 DONE 的唯一原因：C1 让 approval 回路在真实 GA 运行时
**可能完全不触发**，这是 brief 测试盲区掩盖的设计缺陷，需 review 阶段或 Task 6
收尾时核实并（很可能需要）补 `ga.ask_user = _bridge_ask_user` 一行。


# Task 5 C1 Fix Report — patch ga.ask_user too

## 修复摘要

**缺陷 C1**：`_patch_ask_user`（`ga_stdio.py`）原只 monkeypatch `ga_utils.ask_user`，
但 `ga.py:8-12` 用 `from ga_utils import (..., ask_user, ...)` 在模块加载时已把
`ask_user` 绑到 **ga.py 自身模块 globals**。`do_ask_user`（`ga.py:79`）方法体
`result = ask_user(question, candidates)` 走 ga 模块全局查找（LOAD_GLOBAL），
解析到 ga.py namespace 内的**原函数引用**，不是 patched 版本——真实运行时
approval/request 从不发，S4 approval 人机回路失效。

**修法**：在 `_patch_ask_user` 内 `ga_utils.ask_user = _bridge_ask_user` 这行
**之后**追加 `import ga; ga.ask_user = _bridge_ask_user`。**不是**改引擎源码
（`ga.py` / `ga_utils.py` 文件不动），是运行时 monkeypatch 两个模块的属性引用。
原有 `ga_utils.ask_user = _bridge_ask_user` 行保留（双保险：若有其他模块用
`ga_utils.ask_user` 形式调用也覆盖到）。

## TDD 纪律

### RED 命令+结果

追加测试 `test_patch_ask_user_reroutes_ga_module_global` 到
`tests/test_ga_stdio_unit.py`，钉真实路由缺陷。

命令：
```
python -m pytest tests/test_ga_stdio_unit.py -v -k "reroutes_ga_module"
```

结果（RED）：
```
tests/test_ga_stdio_unit.py::test_patch_ask_user_reroutes_ga_module_global FAILED [100%]
...
E       AssertionError: ga.ask_user must be rerouted to bridge version (C1)
E       assert <function ask_user at 0x...> is <function BridgeCore._patch_ask_user.<locals>._bridge_ask_user at 0x...>
...
====================== 1 failed, 36 deselected in 0.58s =======================
```

确认 `ga.ask_user` 仍是原 `ga_utils.ask_user`，非 bridge 版本。

### GREEN 命令+结果

在 `ga_stdio.py` `_patch_ask_user` 内 `ga_utils.ask_user = _bridge_ask_user`
之后追加：
```python
# C1: ga.py:8-12 binds `ask_user` into ga module globals via
# `from ga_utils import ask_user`; do_ask_user (ga.py:79) looks it up
# there (LOAD_GLOBAL), so patching ga_utils.ask_user alone never reaches
# the real do_ask_user path. Reroute ga.ask_user too.
import ga
ga.ask_user = _bridge_ask_user
```

命令：
```
python -m pytest tests/test_ga_stdio_unit.py -v -k "reroutes_ga_module"
```

结果（GREEN）：
```
tests/test_ga_stdio_unit.py::test_patch_ask_user_reroutes_ga_module_global PASSED [100%]
====================== 1 passed, 36 deselected in 0.21s =======================
```

### 全单元测试

命令：
```
python -m pytest tests/test_ga_stdio_unit.py -v
```

结果：
```
============================= 37 passed in 0.32s ==============================
```

37 个（36 既有 + 1 新增），全绿。

## 全量结果

命令：
```
python -m pytest tests/ -q
```

结果：
```
........................................................................ [ 28%]
........................................................................ [ 57%]
........................................................................ [ 86%]
...................................                                      [100%]
251 passed in 33.71s
```

251 passed = 250 既有 + 1 新增，无回归。

## 提交哈希

见本 commit：`fix(ga_stdio): patch ga.ask_user too — do_ask_user routes via ga globals (C1)`

## 变更文件

- `ga_stdio.py`：`_patch_ask_user` 内追加 6 行（注释 + `import ga` + `ga.ask_user = _bridge_ask_user`），位于 `ga_utils.ask_user = _bridge_ask_user` 之后、`self._bridge_ask_user = _bridge_ask_user` 之前。
- `tests/test_ga_stdio_unit.py`：追加 `test_patch_ask_user_reroutes_ga_module_global` 测试 + 章节注释，25 行。
- `.superpowers/sdd/task-5-report.md`：本 fix report 段。

## 风险信号自报

1. **C1 已修**：本 fix 直接修了路由缺陷——`ga.ask_user` 现在 reroute 到 bridge 版本。
   真实 GA 运行时 `do_ask_user`（`ga.py:79`）的 LOAD_GLOBAL 现在解析到 patched 版本，
   approval/request 会正常发出，S4 approval 人机回路恢复。
2. **多实例隔离**（implementer C5 顾虑）**不在本 fix 范围**：
   `_bridge_ask_user` 闭包内 `self` 仍是首次 `_patch_ask_user` 的 BridgeCore 实例
   （`_ask_user_patched` flag 使后续实例的 `_patch_ask_user` 直接 return，复用首次闭包）。
   patch `ga.ask_user` 指向同一闭包函数，`_bridge_ask_user._current_ga` 共用。
   当前 37 个单元测试都用单实例 + 直接 `core._bridge_ask_user(...)` 调用方式，
   未触发多实例冲突。留给 review triage。
3. **测试 fixture 隔离**：新测试用 try/finally 在 finally 内 `ga.ask_user = original`
   恢复全局，避免污染后续测试。但 `ga_utils.ask_user` 仍是首次 patched 的闭包
   （`_ask_user_patched` flag 在进程内累积），测试间不复位——与既有 Task 5 测试相同的
   隔离策略，未引入新风险。
4. **合规**：`ga.py` / `ga_utils.py` / `agent_loop.py` / `llmcore.py` / `agentmain.py`
   **源码未动**；只是运行时 monkeypatch 模块属性。符合 controller 裁决。

## 顾虑

1. 多实例隔离（C5）仍未解，留给 review 阶段 triage。
2. 当前没有"多 BridgeCore 实例并发"测试，C5 风险只是静态分析结论，未运行时验证。
3. `_ask_user_patched` 进程内 flag 不可复位——若未来 BridgeCore 需要按实例替换
   不同闭包，需重构为 per-instance 路由（例如 `ga.ask_user` wrapper 查 current instance）。
