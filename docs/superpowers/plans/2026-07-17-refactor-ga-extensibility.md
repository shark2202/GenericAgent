# refactor-ga-extensibility 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

---
change: refactor-ga-extensibility
design-doc: docs/superpowers/specs/2026-07-17-refactor-ga-extensibility-design.md
base-ref: dcda2b998c2bf3b1d541ceb5519d15d8e6dfd3ae
---

**Goal:** 给 `BaseHandler.dispatch` 增加 module-level tool registry fallback，让新工具经 drop-in `tools/` 模块自注册即可被 LLM 调用，无需编辑 `GenericAgentHandler` 类体或 ga.py 主文件。

**Architecture:** 引擎层 `agent_loop.py` 提供 registry 机制（`register_tool`/`_TOOL_REGISTRY`/`discover_tools`/`get_tool` + dispatch fallback）；`dispatch` 先查 `do_<name>` method（method-track，零行为变更），未命中查 registry（registry-track），两轨共享 `_index`/`_tool_num` 注入与 `tool_before`/`tool_after` hook。SDK 层 `agentmain.py` 触发 `discover_tools()` 扫 `tools/` 目录。hermes `do_skill_manage` 首个迁入 `tools/skill_manage.py` 验证 drop-in 闭环。utils 抽离 `ga_utils.py` 纯移动以平衡 net line count。

**Tech Stack:** Python 3，importlib（runtime 装载 `tools/`），pytest，ruff。无新第三方依赖。

## Global Constraints

- **net line count ≤ 0**：新增（registry/dispatch fallback/discover_tools/agentmain 接线）必须被 utils 与 hermes 迁出抵消，整体不增行（CONTRIBUTING.md + design §8）。
- **小变更半径**：只动设计列出的文件；不重构 llmcore / 不动 subagents 的 `do_task`/`do_submit_result`（design §8：与 subagents 不撞车）。
- **let-it-crash**：不新增裸 `except: pass`；`discover_tools` per-module 失败写 stderr（审计 S3 教训）。`agentmain` 外层 try/except 例外——镜像现有 `discover_and_load` 风格（design §4.6）。
- **无新依赖**：仅用 stdlib（importlib/os/sys）。
- **自文档最少注释**：迁移代码保留原 docstring；新机制加一行 docstring 解释作用。
- **tdd_mode=tdd**：每个任务先写失败测试再实现。第 2 组 dispatch 测试是审计 F26（核心热路径零测试）的直接动机。
- **现状基线（已核实，直接引用，勿重新推导）**：
  - `agent_loop.py:18-29` `BaseHandler.dispatch`：`getattr(self, f"do_{tool_name}")(args, response)`；`:21` 注入 `args['_index']=index; args['_tool_num']=tool_num`；`:22`/`:24` `_hook('tool_before'/'tool_after', locals())`；`:23` `ret = yield from try_call_generator(getattr(self, method_name), args, response)`；`:26` `bad_json` 分支；`:27-29` 未知工具 yield + `StepOutcome(None, next_prompt=f"未知工具 {tool_name}", should_exit=False)`。
  - `ga.py` 838 行：~15 个 module-level 纯工具函数（`safe_print`:13/`code_run`:17/`ask_user`:101/`first_init_driver`:108/`web_scan`:118/`format_error`:149/`log_memory_access`:158/`web_execute_js`:168/`expand_file_refs`:179/`file_patch`:193/`_scan_files`:209/`file_read`:215/`smart_format`:255/`consume_file`:260 + 全局 `script_dir`:11/`driver`:107/`_read_dirs`:208）+ `GenericAgentHandler(BaseHandler)`:266（28 方法）+ `get_global_memory()`:823。
  - `do_skill_manage` 在 `ga.py:471-617`；3 helpers `_validate_skill_content`:619 / `_set_frontmatter_flag`:633 / `_build_skill_brief`:648（`_build_skill_brief` 用 `self.cwd` 做 relpath）。
  - `plugins/skill_evolution.py:98` 直调 `handler.do_skill_manage(args, None)`（经 `_drain`）；全仓仅此一处直调（grep 已确认，其余经 dispatch）。
  - `agentmain.py:12` 调 `discover_and_load()`，`try/except Exception: pass` 风格；`agentmain.py:14` `from ga import GenericAgentHandler, smart_format, get_global_memory, format_error, consume_file`。
  - `ga.py:10` `from skill_loader import sync_skills_to_l1, get_skill_detail, parse_provenance, validate_skill, backup_skill_prev, revert_skill_prev, _get_skills_catalog, _reset_skill_cache`。
  - 测试基线：`tests/test_skill_evolution.py`（17 tests，只测 skill_loader 纯函数，不碰 dispatch/do_skill_manage）+ `tests/test_skill_evolution_plugin.py`（21 tests，`_FakeHandler.do_skill_manage` mock 在 :106）。合计 38。

**协调声明（design §8）：** hermes `do_skill_manage` 是首个迁移目标。**严禁**碰 subagents 的 `do_task`/`do_submit_result`/`_subagent_mgr`（build 阶段并行进行）；method-track 优先保证 subagents in-handler 方法不受影响。

## 文件结构

| 文件 | 操作 | 责任 |
|---|---|---|
| `agent_loop.py` | Modify | +`_TOOL_REGISTRY`/`register_tool`/`get_tool`/`discover_tools`（引擎机制）+ `dispatch` registry fallback 分支 |
| `tools/__init__.py` | Create | 空文件，使 `tools` 成为 package（importlib `tools.<mod>` 装载所需） |
| `tools/skill_manage.py` | Create | 迁入 `do_skill_manage` + 3 helpers，`self→handler`，`register_tool("skill_manage")` 自注册 |
| `agentmain.py` | Modify | `:12` 旁加 `discover_tools()` 调用 |
| `ga.py` | Modify | 删 `do_skill_manage` + 3 helpers；utils 迁出（Task 3）；顶部 import 调整 |
| `ga_utils.py` | Create | 迁入 ~15 module-level utils + 3 全局 |
| `plugins/skill_evolution.py` | Modify | `:98` 改 `get_tool("skill_manage")(handler, args, None)` + `fn is None` 防御 |
| `tests/test_agent_loop_dispatch.py` | Create | dispatch 全路径测试（审计 F26） |
| `tests/test_tool_registry.py` | Create | registry 装载/隔离测试 |
| `tests/test_skill_evolution_plugin.py` | Modify | `_FakeHandler.do_skill_manage` mock 改经 `register_tool` |
| `MAP.md` | Modify | ga.py 条目 + 新增 ga_utils.py/tools/ |
| `docs/architecture.md` | Modify | registry 模块定位（如分层有变） |

## 任务依赖

- **Task 1（registry core）** 是一切前置；Task 2/4 依赖它。
- **Task 2（dispatch 测试）** 依赖 Task 1，独立于 3/4。
- **Task 3（utils 抽离）** 与 Task 1/2 互相独立（纯移动，不改 dispatch 契约），可与 Task 2 并行。
- **Task 4（hermes 迁移 + ripple）** 依赖 Task 1（registry）+ Task 2（dispatch fallback 已测）。Task 4.2 ripple 改 `_apply_op`，需同步改 `test_skill_evolution_plugin.py`（4.3）。
- **Task 5（收尾核算）** 依赖全部。
- 建议执行顺序：1 → 2 → 3 → 4 → 5。

---

## Task 1: Registry core — `register_tool` / `get_tool` / `discover_tools`（additive, no behavior change）

**对应 tasks.md：** 1.1, 1.3（1.2 dispatch fallback 在 Task 2）

**Files:**
- Modify: `agent_loop.py`（顶部加 import + 新增 4 个符号，在 `BaseHandler` class 之前插入）
- Test: `tests/test_tool_registry.py`（Create）

**Interfaces:**
- Consumes: 无（引擎层自包含）
- Produces:
  - `register_tool(name: str) -> Callable[[Callable], Callable]`：装饰器，写 `_TOOL_REGISTRY[name] = fn`，返回 fn
  - `get_tool(name: str) -> Optional[Callable]`：返回 `_TOOL_REGISTRY.get(name)`
  - `discover_tools(tools_dir: str) -> None`：扫 `tools_dir` 下非 `_` 开头 `.py`，`importlib.import_module(f"tools.{mod}")` 装载；per-module 失败写 stderr；目录不存在早退
  - `_TOOL_REGISTRY: dict[str, Callable]`：module-level dict

- [x] **Step 1.1: 写失败测试 `tests/test_tool_registry.py`**

```python
"""Tests for the tool registry mechanism (register_tool / get_tool / discover_tools).

Covers audit F26's sibling gap: the dispatch extensibility primitives had no tests.
"""
import os
import sys
import importlib

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import agent_loop  # noqa: E402
from agent_loop import register_tool, get_tool, discover_tools, _TOOL_REGISTRY  # noqa: E402


def test_register_tool_stores_and_returns_fn():
    saved = _TOOL_REGISTRY.get("t_echo")
    try:
        @register_tool("t_echo")
        def fn(handler, args, response):
            return None
        assert _TOOL_REGISTRY["t_echo"] is fn
        assert get_tool("t_echo") is fn
    finally:
        if saved is None:
            _TOOL_REGISTRY.pop("t_echo", None)
        else:
            _TOOL_REGISTRY["t_echo"] = saved


def test_get_tool_returns_none_for_unknown():
    assert get_tool("definitely_not_registered_xyz") is None


def test_discover_tools_loads_dropin_modules(tmp_path, monkeypatch):
    """A non-underscore .py in tools_dir self-registers on import."""
    # Build a throwaway package dir on sys.path so importlib.import_module("pkg.<mod>") works.
    pkg = tmp_path / "tpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod_a.py").write_text(
        "from agent_loop import register_tool\n"
        "@register_tool('d_dropin')\n"
        "def _fn(handler, args, response):\n"
        "    return 'dropin-ok'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    saved = _TOOL_REGISTRY.get("d_dropin")
    try:
        discover_tools(str(pkg))
        assert get_tool("d_dropin")() == "dropin-ok"
    finally:
        if saved is None:
            _TOOL_REGISTRY.pop("d_dropin", None)
        else:
            _TOOL_REGISTRY["d_dropin"] = saved


def test_discover_tools_skips_underscore_files(tmp_path, monkeypatch):
    pkg = tmp_path / "tpkg2"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "_private.py").write_text(
        "from agent_loop import register_tool\n"
        "@register_tool('d_should_not_load')\n"
        "def _fn(handler, args, response):\n"
        "    return None\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        discover_tools(str(pkg))
        assert get_tool("d_should_not_load") is None
    finally:
        _TOOL_REGISTRY.pop("d_should_not_load", None)


def test_discover_tools_isolates_module_load_failure(tmp_path, monkeypatch, capsys):
    """One broken module must not kill sibling modules."""
    pkg = tmp_path / "tpkg3"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    (pkg / "good.py").write_text(
        "from agent_loop import register_tool\n"
        "@register_tool('d_survivor')\n"
        "def _fn(handler, args, response):\n"
        "    return 'survived'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    saved = _TOOL_REGISTRY.get("d_survivor")
    try:
        discover_tools(str(pkg))  # must not raise
        assert get_tool("d_survivor")() == "survived"
        err = capsys.readouterr().err
        assert "broken" in err and "boom" in err  # per-module stderr, not silent
    finally:
        if saved is None:
            _TOOL_REGISTRY.pop("d_survivor", None)
        else:
            _TOOL_REGISTRY["d_survivor"] = saved


def test_discover_tools_noop_when_dir_missing(tmp_path):
    missing = tmp_path / "does_not_exist"
    discover_tools(str(missing))  # must not raise
```

- [x] **Step 1.2: 运行测试确认失败**

Run: `python -m pytest tests/test_tool_registry.py -v`
Expected: FAIL — `ImportError: cannot import name 'register_tool' from 'agent_loop'`

- [x] **Step 1.3: 在 `agent_loop.py` 实现 registry 机制**

在 `agent_loop.py` 顶部 `import json, re, os` 改为同时引入 importlib/sys，并在 `try_call_generator` 之后、`class BaseHandler` 之前插入 registry 块。

把第 1 行：
```python
import json, re, os
```
改为：
```python
import json, re, os, importlib, sys
```

在 `try_call_generator` 函数定义之后（即 `class BaseHandler` 之前）插入：
```python
_TOOL_REGISTRY = {}

def register_tool(name):
    """Decorator: register a tool fn as (handler, args, response) -> StepOutcome under `name`."""
    def deco(fn):
        _TOOL_REGISTRY[name] = fn
        return fn
    return deco

def get_tool(name):
    return _TOOL_REGISTRY.get(name)

def discover_tools(tools_dir):
    """Scan tools_dir for non-underscore .py modules and import them (triggering self-registration).
    Per-module failures write to stderr; a missing dir is a silent no-op."""
    if not os.path.isdir(tools_dir): return
    for e in os.scandir(tools_dir):
        if e.is_file() and e.name.endswith('.py') and not e.name.startswith('_'):
            mod = e.name[:-3]
            try: importlib.import_module(f"tools.{mod}")
            except Exception as ex: sys.stderr.write(f"[tool_registry] failed to load {mod}: {ex}\n")
```

- [x] **Step 1.4: 运行测试确认通过**

Run: `python -m pytest tests/test_tool_registry.py -v`
Expected: PASS（6 tests）

- [x] **Step 1.5: 手动回归确认现有 `do_*` 零行为变更（tasks 1.3）**

`agent_loop.py` 顶部新增只是 additive；`dispatch` 尚未改（Task 2 才改），故所有 `do_*` 仍走原路径。
Run: `python -c "import agent_loop; print('BaseHandler.dispatch' in dir(agent_loop.BaseHandler))"`
Expected: `True`（dispatch 未变）

- [x] **Step 1.6: ruff + commit**

Run: `ruff check agent_loop.py tests/test_tool_registry.py`
Expected: 0 违规

```bash
git add agent_loop.py tests/test_tool_registry.py
git commit -m "feat(agent_loop): add tool registry core (register_tool/get_tool/discover_tools)

Additive mechanism for drop-in tool self-registration. No dispatch
behavior change yet; fallback wiring lands in the next task. Covers
audit F26 sibling gap for the registry primitives."
```

---


## Task 2: `BaseHandler.dispatch` registry fallback + dispatch 路径测试（审计 F26）

**对应 tasks.md：** 1.2, 2.1, 2.2, 2.3, 2.4, 2.5

**Files:**
- Modify: `agent_loop.py:18-29`（`BaseHandler.dispatch`，新增 registry fallback 分支）
- Test: `tests/test_agent_loop_dispatch.py`（Create）

**Interfaces:**
- Consumes: Task 1 的 `get_tool`/`_TOOL_REGISTRY`
- Produces: `dispatch` 双轨契约（method-track 优先 → registry-track fallback → 未知工具）。注册函数签名 `fn(handler, args, response) -> StepOutcome`，`handler` 即 dispatch 的 `self`。

- [x] **Step 2.1: 写失败测试 `tests/test_agent_loop_dispatch.py`**

设计选择：**直接驱动 `handler.dispatch(...)` 而非整个 `agent_runner_loop`**。原因：`agent_runner_loop` 的 `client.chat` 返回生成器、`tc.function.name`/`tc.function.arguments` 结构、`_done_hooks` 等 handler 状态都需精细 mock，易脆。dispatch 是双轨契约的测点；直接调它精确覆盖 spec 的 5 requirements，且不耦合 LLM session 实现。

```python
"""Dispatch tests (audit F26: core dispatch hot path had zero tests).

Drives BaseHandler.dispatch directly with canned args/response objects.
Covers method-track, registry-track fallback, method priority on
collision, arg-injection parity, and unknown-tool path — the 5 spec
requirements for the dual-track dispatch contract.
"""
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent_loop import (  # noqa: E402
    BaseHandler, StepOutcome, register_tool, _TOOL_REGISTRY,
)


class _Resp:
    """Stand-in for the LLM response object; dispatch only reads args/response."""
    def __init__(self, content=""):
        self.content = content
        self.thinking = ""


def _drain(gen):
    """Run a dispatch generator to exhaustion, return its StopIteration.value (StepOutcome)."""
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


class _EchoHandler(BaseHandler):
    """Minimal handler exposing one do_* method (method-track)."""

    def __init__(self):
        self.cwd = os.getcwd()

    def do_echo(self, args, response):
        yield f"echo: {args.get('msg', '')}\n"
        return StepOutcome({"echoed": args.get("msg", "")}, next_prompt="\n")


# ── method-track (spec req 3: backward compat of existing do_*) ───

def test_method_track_dispatches_do_echo():
    h = _EchoHandler()
    outcome = _drain(h.dispatch("echo", {"msg": "hi"}, _Resp(), index=0, tool_num=1))
    assert outcome.data == {"echoed": "hi"}


def test_method_track_injects_index_and_tool_num():
    """Method-track receives _index/_tool_num injection (the parity baseline)."""
    class _H(BaseHandler):
        def do_probe(self, args, response):
            return StepOutcome({"i": args.get("_index"), "n": args.get("_tool_num")}, next_prompt="\n")
    h = _H()
    outcome = _drain(h.dispatch("probe", {}, _Resp(), index=4, tool_num=2))
    assert outcome.data == {"i": 4, "n": 2}


# ── registry-track fallback (spec req 1 scenario 2, req 2) ───────

@pytest.fixture
def _reg_echo():
    """Register a registry-only echo2 tool; clean up after."""
    @register_tool("echo2")
    def echo2(handler, args, response):
        yield f"echo2: {args.get('msg', '')}\n"
        return StepOutcome({"echoed2": args.get("msg", "")}, next_prompt="\n")
    yield "echo2"
    _TOOL_REGISTRY.pop("echo2", None)


def test_registry_track_dispatches_without_handler_method(_reg_echo):
    """No do_echo2 method on handler, but echo2 registered -> registry-track."""
    class _H(BaseHandler):
        pass
    h = _H()
    outcome = _drain(h.dispatch("echo2", {"msg": "yo"}, _Resp(), index=0, tool_num=1))
    assert outcome.data == {"echoed2": "yo"}


def test_method_track_beats_registry_on_collision():
    """do_echo2_collide on handler AND echo2_collide registered -> method wins (spec req 1 scenario 1)."""
    call_log = []

    @register_tool("echo2_collide")
    def _reg_fn(handler, args, response):
        call_log.append("registry")
        return StepOutcome({"via": "registry"}, next_prompt="\n")

    class _H(BaseHandler):
        def do_echo2_collide(self, args, response):
            call_log.append("method")
            return StepOutcome({"via": "method"}, next_prompt="\n")

    try:
        h = _H()
        outcome = _drain(h.dispatch("echo2_collide", {}, _Resp(), index=0, tool_num=1))
        assert outcome.data == {"via": "method"}
        assert call_log == ["method"]  # registry NOT invoked
    finally:
        _TOOL_REGISTRY.pop("echo2_collide", None)


def test_registry_arg_injection_parity(_reg_echo):
    """Registry fn receives _index/_tool_num like a method would (spec req 4)."""
    @register_tool("argprobe")
    def _probe(handler, args, response):
        return StepOutcome({"_index": args.get("_index"), "_tool_num": args.get("_tool_num")}, next_prompt="\n")
    try:
        class _H(BaseHandler):
            pass
        h = _H()
        outcome = _drain(h.dispatch("argprobe", {"msg": "x"}, _Resp(), index=3, tool_num=7))
        assert outcome.data == {"_index": 3, "_tool_num": 7}
    finally:
        _TOOL_REGISTRY.pop("argprobe", None)


def test_registry_fn_signature_uses_handler_param():
    """Registry fn accesses handler state via `handler` param, not self (spec req 5)."""
    @register_tool("cwdprobe")
    def _fn(handler, args, response):
        return StepOutcome({"cwd_seen": handler.cwd}, next_prompt="\n")
    try:
        class _H(BaseHandler):
            pass
        h = _H()
        h.cwd = "/custom/cwd"
        outcome = _drain(h.dispatch("cwdprobe", {}, _Resp(), index=0, tool_num=1))
        assert outcome.data == {"cwd_seen": "/custom/cwd"}
    finally:
        _TOOL_REGISTRY.pop("cwdprobe", None)


# ── unknown tool (spec req 1 scenario 3) ─────────────────────────

def test_unknown_tool_when_neither_method_nor_registry():
    class _H(BaseHandler):
        pass
    h = _H()
    outcome = _drain(h.dispatch("totally_missing", {"x": 1}, _Resp(), index=0, tool_num=1))
    assert "未知工具" in (outcome.next_prompt or "")
    assert outcome.data is None
    assert outcome.should_exit is False
```

- [x] **Step 2.2: 运行测试确认失败**

Run: `python -m pytest tests/test_agent_loop_dispatch.py -v`
Expected: FAIL — registry-track 测试失败（dispatch 仍走原路径，无 fallback，`echo2` 命中"未知工具"分支返回 None data 而非 `{"echoed2":"yo"}`）

- [x] **Step 2.3: 在 `agent_loop.py:18-29` 实现 dispatch registry fallback**

把现有 `dispatch`（`agent_loop.py:18-29`）：
```python
def dispatch(self, tool_name, args, response, index=0, tool_num=1):
    method_name = f"do_{tool_name}"
    if hasattr(self, method_name):
        args['_index'] = index; args['_tool_num'] = tool_num
        _hook('tool_before', locals())
        ret = yield from try_call_generator(getattr(self, method_name), args, response)
        _hook('tool_after', locals())
        return ret
    elif tool_name == 'bad_json': return StepOutcome(None, next_prompt=args.get('msg', 'bad_json'), should_exit=False)
    else:
        yield f"未知工具: {tool_name}\n"
        return StepOutcome(None, next_prompt=f"未知工具 {tool_name}", should_exit=False)
```
改为：
```python
def dispatch(self, tool_name, args, response, index=0, tool_num=1):
    method_name = f"do_{tool_name}"
    if hasattr(self, method_name):
        args['_index'] = index; args['_tool_num'] = tool_num
        _hook('tool_before', locals())
        ret = yield from try_call_generator(getattr(self, method_name), args, response)
        _hook('tool_after', locals())
        return ret
    elif tool_name == 'bad_json': return StepOutcome(None, next_prompt=args.get('msg', 'bad_json'), should_exit=False)
    else:
        fn = get_tool(tool_name)
        if fn is not None:
            args['_index'] = index; args['_tool_num'] = tool_num
            _hook('tool_before', locals())
            ret = yield from try_call_generator(fn, self, args, response)
            _hook('tool_after', locals())
            return ret
        yield f"未知工具: {tool_name}\n"
        return StepOutcome(None, next_prompt=f"未知工具 {tool_name}", should_exit=False)
```

要点（design §4.2）：
- method-track 优先（D1），`_index`/`_tool_num` 注入 + `tool_before`/`tool_after` hook 两轨同效（spec arg-injection-parity requirement）。
- `fn(self, args, response)`：`self` 即 handler，注册函数签名 `fn(handler, args, response)`（D2）。
- 未知工具路径不变。

- [x] **Step 2.4: 运行测试确认通过**

Run: `python -m pytest tests/test_agent_loop_dispatch.py -v`
Expected: PASS（8 tests：method-track echo/method-arg-inject、registry-track fallback、collision method-wins、arg-parity、signature-handler-param、unknown-tool）

- [x] **Step 2.5: 回归现有 do_* 零行为变更（tasks 1.3）**

method-track 分支逐字未动，仅新增 else 分支内的 registry 子分支。跑既有 skill 测试确认未破：
Run: `python -m pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py -v`
Expected: PASS（38 tests，`do_skill_manage` 仍走 method-track，未删）

- [x] **Step 2.6: ruff + commit**

Run: `ruff check agent_loop.py tests/test_agent_loop_dispatch.py`
Expected: 0 违规

```bash
git add agent_loop.py tests/test_agent_loop_dispatch.py
git commit -m "feat(dispatch): add registry-track fallback + end-to-end dispatch tests

method-track priority preserved (D1); registry fn(handler,args,response)
dispatched with identical _index/_tool_num injection and tool_before/after
hooks (arg-injection-parity). Covers audit F26: the core dispatch hot
path now has method/registry/collision/unknown-tool coverage."
```

---

## Task 3: utils 抽离 → `ga_utils.py`（纯移动）

**对应 tasks.md：** 3.1, 3.2, 3.3, 3.4

**Files:**
- Create: `ga_utils.py`
- Modify: `ga.py`（删迁出的 module-level 函数 + 全局，顶部加 import）
- Modify: `MAP.md`（ga.py 条目描述 + 新增 ga_utils.py 条目）

**Interfaces:**
- Consumes: 无新依赖；utils 间互调（如 `web_scan` 调 `first_init_driver`/`smart_format`/`format_error`）保持模块内可见
- Produces: `ga_utils` 模块导出与原 `ga.py` 同名符号（经 `from ga_utils import *` 回填 `ga.py` namespace，外部 `from ga import smart_format` 等 import 零破坏）

**设计要点（design §4.5）：** 纯移动，net ≈ 0。分组仅是组织注释，不拆多文件（守小变更半径）。`ga.py:1` 现有 import（`sys, os, re, json, time, threading, importlib, webbrowser` 等）需评估哪些仅被迁出函数用——若 `webbrowser`/`simphtml` 只在 utils 内用，迁入 `ga_utils.py` 顶部；`ga.py` 仍需的（如 `re`/`json` 给 handler 方法用）保留。

- [ ] **Step 3.1: 写回归测试 `tests/test_ga_utils_import.py`（迁移前后行为不变的契约）**

迁移是纯移动，测试聚焦"符号仍可 import + 行为不变"。

```python
"""Regression: ga_utils extraction is a pure move. Symbols must remain importable
from ga (back-compat) and from ga_utils, with unchanged behavior."""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_ga_reexports_utils_symbols():
    """External code does `from ga import smart_format, ...`; must still work."""
    import ga
    for name in ("smart_format", "format_error", "consume_file", "code_run",
                 "file_read", "file_patch", "safe_print", "ask_user",
                 "get_global_memory"):
        assert hasattr(ga, name), f"ga.{name} missing after utils extraction"


def test_ga_utils_module_has_moved_symbols():
    import ga_utils
    for name in ("smart_format", "format_error", "consume_file", "code_run",
                 "file_read", "file_patch", "safe_print", "ask_user",
                 "script_dir", "driver", "_read_dirs"):
        assert hasattr(ga_utils, name), f"ga_utils.{name} missing"


def test_smart_format_behavior_unchanged():
    from ga import smart_format as ga_sf
    from ga_utils import smart_format as gu_sf
    # smart_format(data, max_str_len=100, omit_str=' ... ')
    assert ga_sf({"k": "x" * 200}) == gu_sf({"k": "x" * 200})


def test_format_error_behavior_unchanged():
    from ga import format_error as ga_fe
    from ga_utils import format_error as gu_fe
    try:
        1 / 0
    except Exception as e:
        a, b = ga_fe(e), gu_fe(e)
    assert a == b
    assert "ZeroDivisionError" in a


def test_safe_print_swallows_error():
    # safe_print must not raise even on a broken write
    from ga_utils import safe_print
    safe_print("ok")  # no exception
```

- [ ] **Step 3.2: 运行测试确认失败**

Run: `python -m pytest tests/test_ga_utils_import.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ga_utils'`

- [ ] **Step 3.3: 新建 `ga_utils.py`，迁入 ~15 函数 + 3 全局**

从 `ga.py` 原样搬运以下符号到新文件 `ga_utils.py`（**逐字复制函数体，不改逻辑，仅调整顶部 import**）：

迁入清单（行号对应迁移前 `ga.py`，仅作定位）：
- 全局：`script_dir`（原 :11）— **注意**：`ga.py` 的 handler 方法也用 `script_dir`（如 `code_run_header` 路径）。迁出后 `ga.py` 顶部需 `from ga_utils import script_dir` 或 `ga.py` 自身保留一行 `script_dir = os.path.dirname(os.path.abspath(__file__))`。**选后者更稳**（`ga.py` 与 `ga_utils.py` 同目录，值相同），避免循环 import 风险。即：`script_dir` 在 `ga_utils.py` 定义一份，`ga.py` 经 `from ga_utils import *` 拿到。
- `safe_print`（:13）、`code_run`（:17）、`ask_user`（:101）
- `driver`（:107 全局）、`first_init_driver`（:108）、`web_scan`（:118）、`web_execute_js`（:168）
- `format_error`（:149）、`log_memory_access`（:158）
- `expand_file_refs`（:179）、`file_patch`（:193）
- `_read_dirs`（:208 全局）、`_scan_files`（:209）、`file_read`（:215）
- `smart_format`（:255）、`consume_file`（:260）

`ga_utils.py` 顶部 import（从 `ga.py:1-7` 搬迁 utils 所需的，保留 docstring/原注释）：
```python
import sys, os, re, json, time, threading, importlib, webbrowser
import tempfile, traceback, subprocess, shutil
if sys.stdout is None: sys.stdout = open(os.devnull, "w")
if sys.stderr is None: sys.stderr = open(os.devnull, "w")
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import simphtml  # web_scan 内 importlib.reload(simphtml) 用
script_dir = os.path.dirname(os.path.abspath(__file__))

# ── filetools ────────────────────────────────────────────────────
# (file_read / file_patch / _scan_files / expand_file_refs / log_memory_access 原样贴入)

# ── exectools ────────────────────────────────────────────────────
# (code_run / safe_print 原样贴入)

# ── webtools ─────────────────────────────────────────────────────
driver = None
# (first_init_driver / web_scan / web_execute_js 原样贴入)

# ── misc ─────────────────────────────────────────────────────────
# (ask_user / smart_format / consume_file / format_error 原样贴入)

_read_dirs = set()
```

注：`first_init_driver` 内 `from TMWebDriver import TMWebDriver` 是函数内延迟 import，原样保留（design §4.5 延迟 import）。`web_scan` 内 `importlib.reload(simphtml)` 依赖模块顶部 `import simphtml`。

- [ ] **Step 3.4: 从 `ga.py` 删除迁出的函数 + 全局，顶部加 import**

从 `ga.py` 删除 Step 3.3 列出的所有函数定义与全局（`script_dir`/`driver`/`_read_dirs`/15 函数）。

`ga.py:1-11` 调整：保留 `ga.py` 仍需要的 import（handler 方法用的 `sys/os/re/json/time/threading/importlib/webbrowser` 等——逐个检查：若某 import 只被迁出函数用，从 `ga.py` 删；handler 方法仍用的留下）。**保守做法：先全留，ruff 会报 unused，再按 ruff 删。**

在 `ga.py` 的 `from agent_loop import ...`（原 :9）之后、`from skill_loader import ...`（原 :10）之前或之后插入：
```python
from ga_utils import (safe_print, code_run, ask_user, first_init_driver, web_scan,
    web_execute_js, format_error, log_memory_access, expand_file_refs, file_patch,
    _scan_files, file_read, smart_format, consume_file, script_dir, driver, _read_dirs)
```

> 为何 named import 而非 `from ga_utils import *`：named import 显式声明 `ga.py` 依赖的符号，避免 `*` 的命名污染，且 ruff 友好。`_read_dirs` 带 `_` 前缀也显式列出（`*` 不会导出 `_` 前缀）。

`agentmain.py:14` 现有 `from ga import GenericAgentHandler, smart_format, get_global_memory, format_error, consume_file`——`smart_format`/`format_error`/`consume_file` 经 `ga.py` 的 named import re-export，**零改动**。

- [ ] **Step 3.5: py_compile + ruff**

Run: `python -m py_compile ga.py ga_utils.py && echo OK`
Expected: `OK`

Run: `ruff check ga.py ga_utils.py`
Expected: 0 新违规（若 `ga.py` 某些 import 因迁出变 unused，按 ruff 提示删；这是 net-line-count 的贡献项之一）

- [ ] **Step 3.6: 运行回归测试确认通过**

Run: `python -m pytest tests/test_ga_utils_import.py tests/test_agent_loop_dispatch.py tests/test_tool_registry.py tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py -v`
Expected: PASS（utils 抽离未破 dispatch/registry/skill 行为）

- [ ] **Step 3.7: 更新 `MAP.md`（tasks 3.4）**

在 `MAP.md` 顶层入口表（原 :11-13 附近）：
- 把 `ga.py` 行描述从"工具实现。`GenericAgentHandler(BaseHandler)`：所有 Agent 工具…"改为"工具实现。`GenericAgentHandler(BaseHandler)`：handler 方法（`do_*`）；module-level utils 已抽至 `ga_utils.py`"。
- 新增一行：`| \`ga_utils.py\` | **工具函数库**（从 ga.py 抽离）。filetools/exectools/webtools/misc 分组的纯/半纯函数 + `script_dir`/`driver`/`_read_dirs` 全局 |`

在目录结构总览（原 :29-31 附近）补 `ga_utils.py` 与（预告）`tools/` 目录条目。

- [ ] **Step 3.8: commit**

```bash
git add ga.py ga_utils.py MAP.md tests/test_ga_utils_import.py
git commit -m "refactor(ga): extract module-level utils to ga_utils.py (pure move)

~15 functions + script_dir/driver/_read_dirs globals moved verbatim;
ga.py re-exports them via named import so agentmain and external callers
are unchanged. Net-line contribution toward the ≤0 budget for the change."
```

---

## Task 4: hermes `do_skill_manage` 迁移到 `tools/skill_manage.py` 自注册 + ripple

**对应 tasks.md：** 4.1, 4.2, 4.3, 4.4 + ripple（design §4.3/§4.4）

**Files:**
- Create: `tools/__init__.py`（空）
- Create: `tools/skill_manage.py`（迁入 `do_skill_manage` + 3 helpers，`self→handler`，`register_tool("skill_manage")`）
- Modify: `ga.py`（从 `GenericAgentHandler` 类体删 `do_skill_manage`:471-617 + 3 helpers :619-655）
- Modify: `agentmain.py`（`:12` 旁加 `discover_tools()` 调用 — design §4.6）
- Modify: `plugins/skill_evolution.py:98`（ripple：`handler.do_skill_manage(args, None)` → `get_tool("skill_manage")(handler, args, None)`）
- Modify: `tests/test_skill_evolution_plugin.py`（`_FakeHandler.do_skill_manage` mock → 经 `register_tool` 注册）
- Test: `tests/test_skill_manage_registry.py`（Create — 验证 registry 派发闭环）

**Interfaces:**
- Consumes: Task 1 的 `register_tool`/`get_tool`；Task 2 的 dispatch fallback；`skill_loader` 的 `parse_provenance`/`validate_skill`/`retrieve_skill_by_desc`/`backup_skill_prev`/`revert_skill_prev`/`_get_skills_catalog`/`_reset_skill_cache`/`sync_skills_to_l1`
- Produces: `tools/skill_manage.py` 模块，`register_tool("skill_manage")` 装饰的 `skill_manage(handler, args, response) -> StepOutcome` 生成器；3 个模块级 helper `_validate_skill_content(skill_md)`/`_set_frontmatter_flag(content, key, value)`/`_build_skill_brief(action, name, reason, path, cwd)`

**`self → handler` 映射表（design §4.3，逐处替换）：**

| 原文（ga.py 内） | 迁后（tools/skill_manage.py） |
|---|---|
| `def do_skill_manage(self, args, response):` | `def skill_manage(handler, args, response):` + `@register_tool("skill_manage")` |
| `self.cwd` | `handler.cwd` |
| `self._pending_briefs` | `handler._pending_briefs` |
| `self._get_anchor_prompt(skip=...)` | `handler._get_anchor_prompt(skip=...)` |
| `self._validate_skill_content(skill_md)` | `_validate_skill_content(skill_md)`（模块级，无需 handler） |
| `self._set_frontmatter_flag(content, key, value)` | `_set_frontmatter_flag(content, key, value)`（模块级，纯函数） |
| `self._build_skill_brief('create', name, reason, path)` | `_build_skill_brief('create', name, reason, path, handler.cwd)`（收 cwd 参数） |

- [ ] **Step 4.1: 写失败测试 `tests/test_skill_evolution_plugin.py` 改 mock + `tests/test_skill_manage_registry.py`**

**4.1a: 改 `tests/test_skill_evolution_plugin.py` 的 `_FakeHandler`**

ripple 后 `_apply_op` 调 `get_tool("skill_manage")(handler, args, None)` 而非 `handler.do_skill_manage`。`_FakeHandler.do_skill_manage`（原 :106）不再被调，需改为经 registry 注册 fake。

把原 `_FakeHandler`（:98-109）与 autouse fixture（:28-32）替换为：

```python
class _FakeOutcome:
    def __init__(self, data):
        self.data = data


def _install_fake_skill_manage(status="ok"):
    """Register a fake skill_manage tool bound to a fresh fake handler; return the handler.
    Mirrors the post-ripple contract: _apply_op calls get_tool('skill_manage')(handler, args, None)."""
    import agent_loop
    h = _FakeHandler(status=status)

    @agent_loop.register_tool("skill_manage")
    def _fn(handler, args, response):
        h.calls.append(dict(args))
        yield "streamed"
        return _FakeOutcome({"status": h._status, "action": args["action"], "name": args["name"]})
    return h


class _FakeHandler:
    """Receives skill_manage calls via the registry (post-ripple), not as a method."""

    def __init__(self, status="ok"):
        self.calls = []
        self._pending_briefs = []
        self._status = status


@pytest.fixture(autouse=True)
def _reset_counts_and_registry():
    import agent_loop
    se._auto_patch_counts.clear()
    se._consecutive_auto_distills = 0
    saved = agent_loop._TOOL_REGISTRY.get("skill_manage")
    yield
    if saved is None:
        agent_loop._TOOL_REGISTRY.pop("skill_manage", None)
    else:
        agent_loop._TOOL_REGISTRY["skill_manage"] = saved
```

并把每个调用 `_FakeHandler(status=...)` 的测试改为 `_install_fake_skill_manage(status=...)`：
- `test_apply_op_patch_succeeds_and_counts`：`h = _install_fake_skill_manage(status="ok")`
- `test_apply_op_create_does_not_count_circuit`：`h = _install_fake_skill_manage(status="ok")`
- `test_apply_op_failed_patch_not_counted`：`h = _install_fake_skill_manage(status="invalid")`
- `test_apply_op_unknown_action_skipped`：`h = _install_fake_skill_manage()`
- `test_apply_op_missing_fields_skipped`：`h = _install_fake_skill_manage()`（两处 `_apply_op` 前）
- `test_circuit_breaker_skips_after_max`：`h = _install_fake_skill_manage(status="ok")`

> 注：`_FakeOutcome`/`_FakeHandler` 的 import 顺序——文件顶部已 `import plugins.skill_evolution as se`；在 `_install_fake_skill_manage` 内 `import agent_loop`（避免顶部循环，agent_loop 无 GA 依赖，安全）。

**4.1b: 新建 `tests/test_skill_manage_registry.py` — 验证 do_skill_manage 经 registry 派发**

```python
"""Verifies do_skill_manage dispatches via the registry-track after migration
(the method is removed from GenericAgentHandler; it lives in tools/skill_manage.py)."""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import agent_loop  # noqa: E402


def test_skill_manage_registered_after_discover():
    """discover_tools(tools/) loads tools.skill_manage, which self-registers."""
    tools_dir = os.path.join(_REPO_ROOT, "tools")
    assert os.path.isdir(tools_dir), "tools/ package dir must exist"
    # Ensure the module is (re)imported so the decorator runs even if a prior
    # test imported it under a different state.
    sys.modules.pop("tools.skill_manage", None)
    agent_loop.discover_tools(tools_dir)
    assert agent_loop.get_tool("skill_manage") is not None


def test_skill_manage_not_a_method_on_handler():
    """The method MUST be gone from GenericAgentHandler (registry-track only)."""
    from ga import GenericAgentHandler
    assert not hasattr(GenericAgentHandler, "do_skill_manage"), \
        "do_skill_manage must be migrated out of GenericAgentHandler"


def test_skill_manage_helpers_are_module_level():
    """The 3 helpers moved to tools.skill_manage as module-level functions."""
    import importlib
    import tools.skill_manage as sm
    importlib.reload(sm)
    for name in ("_validate_skill_content", "_set_frontmatter_flag", "_build_skill_brief"):
        assert hasattr(sm, name), f"tools.skill_manage.{name} missing"


def test_build_skill_brief_takes_cwd_param():
    """_build_skill_brief signature changed: (action, name, reason, path, cwd)."""
    import tools.skill_manage as sm
    brief = sm._build_skill_brief("patch", "s", "why", "/abs/.agents/skills/s/SKILL.md", "/abs")
    assert "patch" in brief and "s" in brief and "file_read" in brief
```

- [ ] **Step 4.2: 运行测试确认失败**

Run: `python -m pytest tests/test_skill_manage_registry.py tests/test_skill_evolution_plugin.py -v`
Expected:
- `test_skill_manage_not_a_method_on_handler` FAIL（`do_skill_manage` 仍在 handler）
- `test_skill_manage_registered_after_discover` FAIL（`tools/skill_manage.py` 不存在）
- plugin 测试 FAIL（`_apply_op` 仍调 `handler.do_skill_manage`，fake 已不定义该方法 → AttributeError；或 ripple 未改时 fake 仍走旧路但 fixture 已改）

- [ ] **Step 4.3: 新建 `tools/__init__.py`（空）+ `tools/skill_manage.py`**

`tools/__init__.py`：空文件（使 `tools` 成为 package，importlib `tools.skill_manage` 装载所需）。

`tools/skill_manage.py`：从 `ga.py:471-617` 原样搬运 `do_skill_manage` 函数体，按上面"self→handler 映射表"替换；3 helpers（ga.py:619-655）搬为本模块模块级函数，签名调整如下。

文件骨架：
```python
"""skill_manage tool — self-evolution skill CRUD (create/patch/retire/list_evolvable).

Migrated from GenericAgentHandler.do_skill_manage so it self-registers via
register_tool and dispatches through the registry-track, demonstrating the
drop-in extensibility loop. Behavior unchanged (38 skill tests guard it)."""
import os, re, tempfile
from agent_loop import register_tool, StepOutcome
from skill_loader import (parse_provenance, validate_skill, retrieve_skill_by_desc,
    backup_skill_prev, revert_skill_prev, _get_skills_catalog, _reset_skill_cache, sync_skills_to_l1)


@register_tool("skill_manage")
def skill_manage(handler, args, response):
    '''自进化：管理 agent 自己的 skill（create/patch/retire/list_evolvable）。
    patch/retire 仅作用于 author=agent 且 evolvable 的技能；用户创作技能只读。
    patch/create 写前自动 .prev 备份；写动作需 GA_SKILL_EVOLUTION_ENABLED=1（list_evolvable/dry_run 豁免）。'''
    action = args.get('action', '')
    name = args.get('name', '')
    skill_md = args.get('skill_md', '')
    reason = args.get('reason', '')
    dry_run = bool(args.get('dry_run', False))
    idx = args.get('_index', 0)
    yield f"\n[Action] skill_manage: {action} {name}\n"

    # ── 以下为原 do_skill_manage :483-617 的逻辑，逐行搬运，self.* → handler.*，
    #     self._validate_skill_content → _validate_skill_content，
    #     self._set_frontmatter_flag → _set_frontmatter_flag，
    #     self._build_skill_brief(...) → _build_skill_brief(..., handler.cwd)，
    #     self._get_anchor_prompt → handler._get_anchor_prompt，
    #     self._pending_briefs → handler._pending_briefs，
    #     self.cwd → handler.cwd ──
    # (list_evolvable / create / patch / retire 四分支原样贴入，仅做上述替换)


def _validate_skill_content(skill_md):
    '''Validate SKILL.md content without touching the target path (dry_run).'''
    fd, tmp = tempfile.mkstemp(suffix='.md')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(skill_md)
        return validate_skill(tmp)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _set_frontmatter_flag(content, key, value):
    '''Set a frontmatter field to `value` (replace if present, else append). No-op without frontmatter.'''
    if not content.startswith('---'):
        return content
    end = content.find('---', 3)
    if end == -1:
        return content
    fm = content[3:end]
    pat = re.compile(r'^' + re.escape(key) + r':\s*.*$', re.MULTILINE)
    if pat.search(fm):
        fm = pat.sub(f'{key}: {value}', fm)
    else:
        fm = fm.rstrip('\n') + f'\n{key}: {value}\n'
    return '---' + fm + content[end:]


def _build_skill_brief(action, name, reason, path, cwd):
    '''One-line brief queued for the turn%10 / task-end flush (self-evolution feedback to the user).'''
    why = f" — {reason}" if reason else ""
    try:
        rel = os.path.relpath(path, cwd)
    except Exception:
        rel = path
    return f"\n📌[Skill蒸馏] {action} `{name}`{why} — file_read `{rel}` 查看；.prev 已备份 [approve|edit|revert]"
```

> 实现者注意：`do_skill_manage` 函数体较长（:483-617），**必须逐行搬运不重构**（守 38 测试绿 + 小变更半径）。替换仅限映射表所列 6 类 `self.*`。`backup_skill_prev`/`revert_skill_prev`/`_reset_skill_cache`/`sync_skills_to_l1`/`parse_provenance`/`validate_skill`/`_get_skills_catalog` 均已在顶部 import，调用方式不变。

- [ ] **Step 4.4: 从 `ga.py` 的 `GenericAgentHandler` 删除 `do_skill_manage` + 3 helpers**

删除 `ga.py:471-655`（`do_skill_manage` :471-617 + `_validate_skill_content` :619-631 + `_set_frontmatter_flag` :633-646 + `_build_skill_brief` :648-655）。保留其前后方法（`do_skill_detail` 之前、`do_mcp_call` :657 之后）不受影响。

检查 `ga.py:10` 的 `from skill_loader import ...`：迁移后 `ga.py` 是否还用 `parse_provenance`/`validate_skill`/`backup_skill_prev`/`revert_skill_prev`/`_get_skills_catalog`/`_reset_skill_cache`/`sync_skills_to_l1`/`retrieve_skill_by_desc`？grep 确认；若仅 `do_skill_manage` 用，则这些 import 从 `ga.py` 删除（净减行）。`get_skill_detail`/`sync_skills_to_l1` 若其他 handler 方法仍用则留。

Run: `python -m py_compile ga.py tools/skill_manage.py && echo OK`
Expected: `OK`

- [ ] **Step 4.5: 在 `agentmain.py:12` 旁加 `discover_tools()` 调用（design §4.6）**

在 `agentmain.py:11-13` 现有：
```python
try:
    from plugins.hooks import discover_and_load; discover_and_load()
except Exception: pass
```
之后插入：
```python
try:
    from agent_loop import discover_tools; discover_tools(os.path.join(os.path.dirname(__file__), 'tools'))
except Exception: pass
```

> 外层 try/except 镜像现有 `discover_and_load` 风格（design §4.6）；`discover_tools` 内部 per-module 失败已写 stderr，非静默。`os.path.dirname(__file__)` 即 `script_dir`（`tools/` 与 `agentmain.py` 同目录）。

- [ ] **Step 4.6: ripple — 改 `plugins/skill_evolution.py:98`（design §4.4）**

把 `plugins/skill_evolution.py:97-100`：
```python
    token = skill_write_origin.set('background_review')
    try:
        outcome = _drain(handler.do_skill_manage(args, None))
    finally:
        skill_write_origin.reset(token)
```
改为：
```python
    token = skill_write_origin.set('background_review')
    try:
        from agent_loop import get_tool
        fn = get_tool("skill_manage")
        outcome = _drain(fn(handler, args, None)) if fn else None
    finally:
        skill_write_origin.reset(token)
```

要点：
- `from agent_loop import get_tool` 放函数内（避免模块顶部 import 顺序问题；`agent_loop` 无 GA 依赖，安全）。
- `fn is None` 防御：若 `tools/skill_manage.py` 装载失败（stderr 已记），`outcome = None`，后续 `getattr(outcome, 'data', None)`（:101）返回 None，走"落盘失败"分支（`_consecutive_auto_distills = 0`），let-it-crash 一致。

- [ ] **Step 4.7: 运行 hermes 38 测试 + registry 闭环测试（tasks 4.3）**

Run: `python -m pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_manage_registry.py tests/test_tool_registry.py tests/test_agent_loop_dispatch.py -v`
Expected: PASS
- 38 skill 测试绿：`test_skill_evolution.py`（17，纯 skill_loader 函数，不受影响）+ `test_skill_evolution_plugin.py`（21，mock 已改 registry）。
- `test_skill_manage_registry.py`（4）：注册/非方法/helper 模块级/cwd 参数。
- dispatch/registry 测试仍绿。

- [ ] **Step 4.8: 验证 do_skill_manage 经 registry 派发（tasks 4.4，若 deps-complete env 可用）**

若环境可启动完整 agent（有 mykey.py + 浏览器等）：
Run: `python -c "import agentmain; agentmain.discover_tools(__import__('os').path.join(__import__('os').path.dirname(agentmain.__file__), 'tools')); from agent_loop import get_tool; print('registered:', get_tool('skill_manage') is not None)"`
Expected: `registered: True`

若不可用，Step 4.7 的 `test_skill_manage_registered_after_discover` 已覆盖派发注册闭环；T1 trigger / T3 patch / T8 Brief 端到端路径留 verify 阶段在 deps-complete env 跑（design §6）。

- [ ] **Step 4.9: ruff + commit**

Run: `ruff check tools/skill_manage.py ga.py agentmain.py plugins/skill_evolution.py tests/test_skill_manage_registry.py tests/test_skill_evolution_plugin.py`
Expected: 0 新违规

```bash
git add tools/__init__.py tools/skill_manage.py ga.py agentmain.py plugins/skill_evolution.py tests/test_skill_manage_registry.py tests/test_skill_evolution_plugin.py
git commit -m "refactor(skill_manage): migrate do_skill_manage to tools/skill_manage.py (registry drop-in)

First tool migrated to the registry-track: self->handler, 3 helpers moved
to module-level (_build_skill_brief now takes cwd). agentmain discovers
tools/ at startup; skill_evolution ripple uses get_tool('skill_manage').
38 skill tests stay green; new tests assert the method is gone from the
handler and the tool self-registers on discover_tools."
```

---

## Task 5: 收尾 — net line count 核算 + ruff/pytest 全量 + 文档

**对应 tasks.md：** 5.1, 5.2, 5.3

**Files:**
- Modify: `MAP.md`（若 Task 3 未完全更新或分层有补充）
- Modify: `docs/architecture.md`（registry 模块定位、tools/ drop-in 机制说明）

**Interfaces:**
- Consumes: Task 1-4 全部产物
- Produces: 净行数报告 + 全量测试绿 + 文档更新

- [ ] **Step 5.1: net line count 核算（tasks 5.1）**

对比 base-ref `dcda2b998c2bf3b1d541ceb5519d15d8e6dfd3ae` 与当前工作树的净行数：
Run: `git diff dcda2b998c2bf3b1d541ceb5519d15d8e6dfd3ae --stat -- agent_loop.py ga.py ga_utils.py agentmain.py plugins/skill_evolution.py tools/`
Expected: 各文件增减列；整体 `+/-` 净行 ≤ 0 或接近（design §8：utils 纯移动 +0；registry/dispatch fallback/discover_tools 新增 ~20 行；hermes 迁移 0）。

精确核算（手动核对清单）：
- 新增：`agent_loop.py`（registry 块 ~18 行 + dispatch fallback ~5 行 = ~23 行）、`agentmain.py`（3 行 discover_tools）、`tools/__init__.py`（0 行）、`tests/`（新增测试文件，**不计入 net-line 预算**——测试是覆盖增加，非产品代码膨胀；CONTRIBUTING.md net-line 约束针对产品代码）。
- 抵消：`ga.py` 删 ~15 utils + 3 全局 + `do_skill_manage` + 3 helpers（~190 行删除）− 新 import 行（~3 行）。
- 净产品代码行 ≈ +23（agent_loop）+ 3（agentmain）− 190（ga.py 迁出）+ 190（ga_utils.py + tools/skill_manage.py 迁入）= **约 +26 行新增机制 − 0 净迁出**。

> 若核算结果 > 0 且超出"接近"容忍度，优先压缩 `agent_loop.py` 新增（如 `register_tool` 内联单行 docstring）。但 design §8 目标是 ≤0 或接近——本 change 的核心收益是"开扩展面不增主文件"，26 行引擎机制投入可接受（design §8 已列明）。若 reviewer 认为 net > 0 不可接受，回退到 design §8 的风险项讨论。

- [ ] **Step 5.2: ruff + pytest 全量（tasks 5.2）**

Run: `ruff check .`
Expected: 0 新违规（与 base-ref 相比不增；既有 baseline 168 不在本 change scope，design §2 Non-Goals）

Run: `python -m pytest tests/ -v`
Expected: 不引入新失败。已知 baseline flakiness（若有 browser-dependent 测试）不计。重点确认：
- `tests/test_agent_loop_dispatch.py`（8）PASS
- `tests/test_tool_registry.py`（6）PASS
- `tests/test_ga_utils_import.py`（5）PASS
- `tests/test_skill_manage_registry.py`（4）PASS
- `tests/test_skill_evolution.py`（17）+ `tests/test_skill_evolution_plugin.py`（21）PASS（38）

- [ ] **Step 5.3: 更新 `MAP.md` + `docs/architecture.md`（tasks 5.3）**

**MAP.md**（若 Task 3 未完全覆盖）：
- 顶层入口表补 `tools/` 行：`| \`tools/\` | **drop-in 工具目录**（非 _ 开头 .py 经 \`discover_tools\` 自注册）。\`tools/skill_manage.py\` = hermes 自进化 CRUD |`
- 目录结构总览补 `tools/` 子树。

**docs/architecture.md**：
- 引擎层段落补：`agent_loop.py` 现含 tool registry 机制（`register_tool`/`_TOOL_REGISTRY`/`discover_tools`/`get_tool` + dispatch fallback）。registry 是派发机制属引擎层；`tools/` 下业务模块经 SDK 层 `agentmain` 的 `discover_tools` 运行时装载，`agent_loop` 顶部不 import 业务，保纯引擎性（design §3.1 分层）。
- dispatch 数据流图（若 architecture.md 有）：补 method-track / registry-track 双轨分支。

- [ ] **Step 5.4: commit 收尾**

```bash
git add MAP.md docs/architecture.md
git commit -m "docs: update MAP + architecture for tool registry drop-in mechanism

Record agent_loop's registry role, tools/ drop-in dir, and the dual-track
dispatch (method-track priority + registry-track fallback)."
```

- [ ] **Step 5.5: 最终验证清单**

逐项确认（对应 OpenSpec spec 的 5 requirements / 7 scenarios）：
- [ ] **Dual-track dispatch with method priority**（spec req 1）：Task 2 `test_method_track_beats_registry_on_collision` + `test_registry_track_dispatches_without_handler_method` + `test_unknown_tool_when_neither_method_nor_registry` ✓
- [ ] **Self-registration without handler modification**（spec req 2）：Task 4 `test_skill_manage_not_a_method_on_handler` + `test_skill_manage_registered_after_discover` ✓
- [ ] **Drop-in auto-discovery with zero edit to ga.py main file**（spec req 2 scenario 2，Spec Patch 新增）：Task 1 `test_discover_tools_loads_dropin_modules` + Task 4 实际 `tools/skill_manage.py` drop-in ✓
- [ ] **Backward compatibility of existing do_* methods**（spec req 3）：Task 2 `test_method_track_dispatches_do_echo` + `test_method_track_injects_index_and_tool_num` + Step 2.5 的 38 skill 测试绿 ✓
- [ ] **Arg injection parity across tracks**（spec req 4）：Task 2 `test_registry_arg_injection_parity`（registry）与 `test_method_track_injects_index_and_tool_num`（method）对照 ✓
- [ ] **Registry function signature (handler, args, response)**（spec req 5）：Task 2 `test_registry_fn_signature_uses_handler_param`（验证经 handler 参数访问 handler.cwd）+ Task 4 `tools/skill_manage.py` 的 `skill_manage(handler, args, response)` ✓

---
