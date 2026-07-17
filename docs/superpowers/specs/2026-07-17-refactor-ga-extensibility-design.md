---
comet_change: refactor-ga-extensibility
role: technical-design
canonical_spec: openspec
archived-with: 2026-07-17-refactor-ga-extensibility
status: final
---

# Design Doc — refactor-ga-extensibility（tool 自注册 registry）

> 本文是对 open 阶段 `openspec/changes/refactor-ga-extensibility/design.md`（高层框架，D1–D5）的**深度技术细化**。高层决策、方案选型见 open design.md；本文聚焦实现设计、技术风险、边界条件、测试策略。规范事实源仍为 OpenSpec delta spec `specs/tool-dispatch/spec.md`。

## 1. Context

ga.py（~840 行）= ~15 个 module-level 纯工具函数 + `GenericAgentHandler(BaseHandler)` god-class（28 方法）+ `get_global_memory()`。派发在 `agent_loop.py:18-29` `BaseHandler.dispatch`：`getattr(self, f"do_{tool_name}")(args, response)`，约定式（非 if/elif），`tool_before`/`tool_after` hooks 包裹，`args` 注入 `_index`/`_tool_num`（`:21`）。

现状基线（已核实）：`.gitignore` 缺 `.ga/`（含 `.ga_data/` 不含 `.ga/`）；`agentmain.py:24` 把 `tools_schema` 当字符串加载，无 `GA_` env 处理；`agentmain.py:12` 调 `plugins.hooks.discover_and_load()` 扫 `plugins/` 自动装载事件 hook。`plugins/skill_evolution.py:98` 直调 `handler.do_skill_manage(args, None)`（经 `_drain`）。`do_skill_manage` 在 `ga.py:471-617`，helpers `_validate_skill_content`(`619`)/`_set_frontmatter_flag`(`633`)/`_build_skill_brief`(`648`)。

约束（`CONTRIBUTING.md`）：net line ≤0、小变更半径、let-it-crash（无裸 `except:pass`）、无新依赖、自文档最少注释。先验：`docs/ga-refactor-strategy.md`、`docs/audits/audit-report-genericagent-2026-07-16.html`（F26 核心热路径零测试、S3 裸 except 反模式）、`docs/superpowers/specs/2026-07-16-add-first-class-subagents-design.md`（协调面）。

## 2. Goals / Non-Goals

见 open design.md。简述：Goals = 新增工具不改 handler 类体或 ga.py 主文件（drop-in auto-discovery）+ 双轨派发现有 `do_*` 零行为变更 + hermes `do_skill_manage` 迁注册函数后 38 单测仍绿 + dispatch 路径有测试；Non-Goals = 授权门（Phase 2a）/ llmcore 重构 / 168 ruff baseline / CI / 替 subagents 改方案。

## 3. Architecture

### 3.1 模块拓扑

```
仓库根/
├── agent_loop.py          # +register_tool +_TOOL_REGISTRY +BaseHandler.dispatch fallback +discover_tools +get_tool
├── agentmain.py           # +discover_tools() 调用 (line 12 旁, 扫 tools/)
├── ga.py                  # 瘦身: utils 迁出, do_skill_manage 迁出; 顶部 from ga_utils import * + from tools.skill_manage import *
├── ga_utils.py            # NEW, ~15 module utils (filetools/exectools/webtools/misc 分组) + script_dir/driver/_read_dirs
├── tools/                 # NEW, drop-in 工具目录 (非 _ 开头 .py 自动装载)
│   └── skill_manage.py    # 迁入 do_skill_manage + 3 helpers, self→handler, register_tool("skill_manage")
└── plugins/skill_evolution.py  # ripple: line 98 改 get_tool("skill_manage")(handler,args,None)
```

**分层（守 `docs/architecture.md`）**：引擎层 `agent_loop.py` 只提供机制（`register_tool`/`_TOOL_REGISTRY`/`discover_tools`/`get_tool` + dispatch fallback）——registry 是派发机制，属引擎层，不涉 GA 业务；SDK 层 `agentmain.py` 触发 `discover_tools()` 扫 `tools/`；`tools/` 下模块是 GA 业务（import skill_loader 等），但引擎只经 `discover_tools` 运行时 importlib 装载，不在 `agent_loop.py` 顶部 import，故 `agent_loop` 仍纯引擎。

### 3.2 数据流

```
LLM tool_call(skill_manage) ─▶ agent_runner_loop ─▶ handler.dispatch("skill_manage", args, response, index, tool_num)
                                                                      │
                                   ┌──────────────────────────────────┼──────────────────────────────────┐
                                   ▼                                                                     ▼
                          getattr(self,"do_skill_manage")                                    _TOOL_REGISTRY["skill_manage"]
                          命中 → method-track (现有 do_* 走这)                                未命中 → registry-track fallback
                                   │                                                                     │
                                   └──────────────────────────────┬──────────────────────────────────────┘
                                                                  ▼
                                          args['_index']=index; args['_tool_num']=tool_num (两轨同效, 已有 :21)
                                          _hook('tool_before'/'tool_after', locals()) (两轨同效)
                                          fn(handler, args, response) → StepOutcome
```

现有 `do_*`（`do_code_run`/`do_file_*`/`do_web_*` 等）走 method-track，零行为变更。`do_skill_manage` 迁出后走 registry-track。

## 4. Detailed Design

### 4.1 `tool_registry`（agent_loop.py 内）

```python
import importlib, os, sys
_TOOL_REGISTRY = {}

def register_tool(name):
    def deco(fn):
        _TOOL_REGISTRY[name] = fn
        return fn
    return deco

def get_tool(name):
    return _TOOL_REGISTRY.get(name)

def discover_tools(tools_dir):
    """扫 tools_dir 下非 _ 开头 .py, importlib 装载(触发模块级 register_tool 自注册)。per-module try, 失败写 stderr。"""
    if not os.path.isdir(tools_dir): return
    for e in os.scandir(tools_dir):
        if e.is_file() and e.name.endswith('.py') and not e.name.startswith('_'):
            mod = e.name[:-3]
            try: importlib.import_module(f"tools.{mod}")
            except Exception as ex: sys.stderr.write(f"[tool_registry] failed to load {mod}: {ex}\n")
```

- **`register_tool`**：装饰器，写 `_TOOL_REGISTRY[name]`。模块 import 时（经 `discover_tools`）触发自注册。
- **`get_tool(name)`**：lookup helper，供 `plugins/skill_evolution.py` 等需绕过 dispatch 直调注册工具处用。
- **`discover_tools`**：镜像 `plugins/hooks.py:46 discover_and_load` 模式，但 per-module 失败写 stderr（非静默 `except:pass`，守 let-it-crash + 审计 S3 教训）。

### 4.2 `BaseHandler.dispatch` fallback（agent_loop.py:18-29）

```python
def dispatch(self, tool_name, args, response, index=0, tool_num=1):
    method_name = f"do_{tool_name}"
    if hasattr(self, method_name):
        args['_index'] = index; args['_tool_num'] = tool_num
        _hook('tool_before', locals())
        ret = yield from try_call_generator(getattr(self, method_name), args, response)
        _hook('tool_after', locals())
        return ret
    elif tool_name == 'bad_json': return StepOutcome(None, next_prompt=args.get('msg','bad_json'), should_exit=False)
    else:
        # NEW: registry-track fallback
        fn = get_tool(tool_name)
        if fn is not None:
            args['_index'] = index; args['_tool_num'] = tool_num
            _hook('tool_before', locals())
            ret = yield from try_call_generator(fn, self, args, response)   # fn(handler,args,response)
            _hook('tool_after', locals())
            return ret
        yield f"未知工具: {tool_name}\n"
        return StepOutcome(None, next_prompt=f"未知工具 {tool_name}", should_exit=False)
```

- method-track 优先（D1）；`_index`/`_tool_num` 注入 + `tool_before`/`tool_after` hook 两轨同效（spec arg-injection-parity requirement）。
- `fn(self, args, response)`：`self` 即 handler，注册函数签名 `fn(handler, args, response)`（D2）。

### 4.3 `tools/skill_manage.py`（hermes 迁移）

```python
import os, re
from agent_loop import register_tool, StepOutcome
from skill_loader import (parse_provenance, validate_skill, retrieve_skill_by_desc,
    backup_skill_prev, revert_skill_prev, _get_skills_catalog, _reset_skill_cache, sync_skills_to_l1)

@register_tool("skill_manage")
def skill_manage(handler, args, response):
    # gen-style: 镜像原 do_skill_manage(self,args,response), self→handler
    action = args.get('action',''); name = args.get('name',''); skill_md = args.get('skill_md','')
    reason = args.get('reason',''); dry_run = bool(args.get('dry_run',False)); idx = args.get('_index',0)
    yield f"\n[Action] skill_manage: {action} {name}\n"
    # ... (原 do_skill_manage 逻辑, self.* → handler.*:
    #      self.cwd→handler.cwd, self._pending_briefs→handler._pending_briefs,
    #      self._get_anchor_prompt→handler._get_anchor_prompt,
    #      self._validate_skill_content/_set_frontmatter_flag/_build_skill_brief→本模块内联或本模块定义)
    ...
```

- `do_skill_manage` 的 3 helpers（`_validate_skill_content`/`_set_frontmatter_flag`/`_build_skill_brief`）一并迁入本模块，从 `self.` 方法降为模块级函数（收 `handler`/`cwd` 参数）。`_build_skill_brief` 原用 `self.cwd` 做 relpath → 改收 `cwd` 参数。
- `self._get_anchor_prompt(skip=...)` → `handler._get_anchor_prompt(skip=...)`（保留在 handler，是 prompt 基建）。
- provenance gate / `.prev` / `validate_skill` / `_reset_skill_cache` / `sync_skills_to_l1` / `_pending_briefs` append —— 全经 handler/skill_loader，行为不变（38 单测验证）。

### 4.4 ripple: `plugins/skill_evolution.py:98`

```python
# 原: outcome = _drain(handler.do_skill_manage(args, None))
# 改:
from agent_loop import get_tool
fn = get_tool("skill_manage")
outcome = _drain(fn(handler, args, None)) if fn else None
```

1 行 + 防御 `fn is None`。`get_tool` 本就是 §4.1 的 lookup helper，非额外成本。

### 4.5 `ga_utils.py`（utils 抽离，纯移动）

按分组移入 ~15 函数 + `script_dir`/`driver`/`_read_dirs` 全局：
- filetools：`file_read`/`file_patch`/`_scan_files`/`expand_file_refs`/`log_memory_access`
- exectools：`code_run`/`stream_reader`/`safe_print`
- webtools：`web_scan`/`web_execute_js`/`first_init_driver` + `driver` 全局 + `simphtml`/`TMWebDriver` 延迟 import
- misc：`ask_user`/`smart_format`/`consume_file`/`format_error`

`ga.py` 顶部 `from ga_utils import *`（或 named import 保可读）。net ≈ 0（纯移动）。

### 4.6 `agentmain.py` 接线

`agentmain.py:12` 旁加：
```python
try:
    from agent_loop import discover_tools; discover_tools(os.path.join(os.path.dirname(__file__), 'tools'))
except Exception: pass
```
（守现有 `discover_and_load` 的 try/except 风格；`discover_tools` 内部 per-module 已写 stderr。）

## 5. Error Handling / Boundary Conditions

| 场景 | 处理 |
|---|---|
| method 与 registry 同名 | method 优先（D1），registry 不被调（测试覆盖） |
| registry fn 抛错 | 经 `try_call_generator` 传播，与 method 一致，let-it-crash |
| `tools/` 单模块 import 失败 | per-module try/except，写 stderr 一行（不杀其他工具） |
| `tools/` 目录不存在 | `discover_tools` 早退（`os.path.isdir` 守卫） |
| 未知工具（无 method 无 registry） | 现有"未知工具"路径不变 |
| `get_tool(name)` 返回 None（skill_evolution ripple） | §4.4 防御 `fn is None`，不调 |

## 6. Test Strategy

**单测（不需 deps-complete env）**：
- `tests/test_agent_loop_dispatch.py`（审计 F26）：mock client + canned responses + test handler(`do_echo` method) + 一个 registry 注册的 `echo2`。覆盖：单 tool→done / MAX_TURNS / `should_exit` / `no_tool` / `_done_hooks` / `tool_results` 组装 / method-track 派发 / registry-track fallback / method 优先碰撞 / `_index`·`_tool_num` 注入 parity / 未知工具。
- `tests/test_tool_registry.py`：`register_tool` 注册 / `get_tool` lookup / `discover_tools` 扫 fixture `tools/` 目录装载 + 单模块失败隔离。
- hermes 38 单测仍绿（`do_skill_manage` 经 registry 派发，provenance/`.prev`/熔断不变）。
- `test_mcp_ga_integration.py` 不受影响（`do_mcp_call` 留 method-track）。

**验收**：`ruff check` 新代码 0 违规；`pytest tests/` 不引入新失败（已知 baseline flakiness 不计）；ga.py net line count ≤0 或接近。

## 7. Design Decisions Deepened（对 open design.md）

| 决策 | open design.md | 本 Doc 深化 |
|---|---|---|
| 加载机制 | （未明，open question） | **auto-discovery**（复用 discover_and_load 模式扫 `tools/`），真·不改 ga.py 主文件 |
| registry 模块位置 | open question（agent_loop 内 vs 独立） | **agent_loop.py 内**（registry 是引擎机制，保 agent_loop 纯引擎性；discover_tools 由 SDK 触发） |
| 签名 | D2 倾向 fn(handler,args,response) | **确认 fn(handler,args,response)**，`dispatch` 调 `fn(self,args,response)` |
| ripple 处理 | （未提） | `plugins/skill_evolution.py:98` 改 `get_tool` lookup + `fn is None` 防御 |
| discover_tools 失败 | （未提） | per-module try + stderr（非静默，守 let-it-crash + 审计 S3） |

## 8. Risks

- [迁移 ripple 遗漏其他直调点] → grep `do_skill_manage` 全仓确认仅 skill_evolution.py:98 一处直调（其余经 dispatch）。
- [net line ≤0] → utils 纯移动 +0；registry/dispatch fallback/discover_tools 新增 ~20 行；hermes 迁移 0。整体目标 ≤0，build 阶段核算。
- [`agent_loop` 纯引擎性] → registry 是派发机制属引擎；`tools/` 业务模块经 SDK 层 `discover_tools` 运行时装载，`agent_loop` 顶部不 import 业务，保分层。
- [与 subagents 撞车] → hermes 迁移不碰 `do_task`/`do_submit_result`/`_subagent_mgr`；method 优先保 subagents in-handler 方法不受影响。

## 9. Out of Scope / 下一 change

- per-tool 授权门 + 路径收容（审计 F1 Critical / F2 / F3）——下一 change，挂在本 registry 上。
- 迁其他 `do_*`（`do_mcp_call` 等）到 registry——follow-up，非本 change。
- CI gates（审计 F13）——独立 change。

## 10. Mapping to OpenSpec Spec

delta spec `specs/tool-dispatch/spec.md` 的 5 Requirement / 6 Scenario 覆盖本设计的派发契约。**Spec Patch**：给 "Self-registration without handler modification" requirement **加 1 scenario** 明确 drop-in auto-discovery（丢 `tools/` 文件即加载，零编辑 ga.py 主文件），使 spec 与 proposal success criteria #1 对齐、锁加载机制 A。属补充验收场景，不大改 spec 结构。

