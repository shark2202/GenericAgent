## Context

ga.py（~840 行）= ~15 个 module-level 纯工具函数 + `GenericAgentHandler(BaseHandler)` god-class（28 方法）+ `get_global_memory()`。派发机制在 `agent_loop.py:18-29` `BaseHandler.dispatch`：`getattr(self, f"do_{tool_name}")(args, response)`，已是约定式（非 if/elif），`tool_before`/`tool_after` hooks 包裹。现状基线已核实：`.gitignore` 缺 `.ga/`（含 `.ga_data/` 不含 `.ga/`）；`agentmain.py:24` 无 `GA_` env 处理。

约束（`CONTRIBUTING.md`）：net line count ≤0、小变更半径、let-it-crash、无新依赖、自文档最少注释。先验：`docs/ga-refactor-strategy.md`（phase 计划）、`docs/audits/audit-report-genericagent-2026-07-16.html`（58-finding 审计，F26 核心热路径零测试）、`docs/architecture.md`、`MAP.md`（2026-07-08 stale）。

## Goals / Non-Goals

**Goals:**
- 新增工具不需改 `GenericAgentHandler` 类体或 ga.py 主文件——在独立模块 `register_tool('name')(fn)` 自注册即可。
- 现有 `do_*` 方法零行为变更（向后兼容）。
- hermes `do_skill_manage` 迁到注册函数后，38 单测仍绿、provenance gate/`.prev`/熔断行为不变。
- dispatch 路径有测试（审计 F26）。

**Non-Goals:**
- per-tool 授权门（审计 F1/F2/F3，Phase 2a）→ 下一 change。
- 重构 `llmcore.py` god-module（1964 行）→ 独立 change。
- 清 168 条 ruff baseline（repo 级未来任务）。
- CI gates（审计 F13）→ 独立 change。
- 替 `add-first-class-subagents` 改方案（它继续 in-handler，双轨兼容）。

## Decisions

**D1 双轨派发（method-track 优先，registry-track fallback）。** `dispatch` 先 `getattr(self,"do_<name>")`，未命中再查 module-level `_TOOL_REGISTRY`。
- *备选*：纯 registry（强制迁移所有 `do_*`）→ 否决：破坏 `add-first-class-subagents`（build 中，in-handler `do_task`/`do_submit_result`）+ 巨大变更半径，违 CONTRIBUTING 小半径。
- *备选*：registry 优先、method fallback → 否决：现有 `do_*` 行为依赖 method 优先语义（如 `no_tool` 特殊处理在 `do_no_tool`），改优先级引入隐性行为变更。

**D2 registry API 签名 = `register_tool(name)` 装饰 `fn(handler, args, response) -> StepOutcome`。** 镜像现有 `do_*(self, args, response)`，`handler` 替代 `self`——最小迁移成本，hermes `do_skill_manage` 可近乎机械迁移。
- *备选*：`fn(args, response, ctx)` 显式 ctx 对象 → 否决：`do_*` 已是 `(self,args,response)`，双签名增加心智负担；`ctx` 对象需新定义，违无新依赖/小半径。`handler` 参数让注册函数能访问 `cwd`/`_pending_briefs`/`_get_anchor_prompt` 等（hermes 迁移所需）。
- dispatch 往 `args` 注入 `_index`/`_tool_num`（`agent_loop.py:21`）对 registry 路径同样生效，保持一致。

**D3 hermes `do_skill_manage` 作首个迁移目标。** 已存在 38 单测（`test_skill_evolution.py` + `test_skill_evolution_plugin.py`），迁移后跑绿即验证 registry 不破行为。
- *备选*：迁 `add-first-class-subagents` 的 `do_task` → 否决：它 build 中（0/31），抢同一份 ga.py，撞车。
- *备选*：新建测试工具 `do_echo` 验证 → 仅验证派发，不验证真实 handler 依赖面（`cwd`/`_pending_briefs` 访问）；hermes 同时覆盖两者。`do_echo` 仍作为 dispatch 测试夹具。

**D4 utils 抽离到单文件 `ga_utils.py`，非 `ga_tools/` 包。** 单文件最小半径；包结构对当前 ~15 函数 over-engineering。
- 分组（同文件内注释分隔，非子模块）：filetools（`file_read`/`file_patch`/`_scan_files`/`expand_file_refs`/`log_memory_access`）、exectools（`code_run`/`stream_reader`/`safe_print`）、webtools（`web_scan`/`web_execute_js`/`first_init_driver` + `driver` 全局）、misc（`ask_user`/`smart_format`/`consume_file`/`format_error`）。
- `script_dir` 移入 `ga_utils.py` 或由 handler 注入。

**D5 不做授权门（留下一 change）。** 审计 F1（Critical，`inline_eval` 暴露 `api_key`）/F2/F3 是安全维度；本 change 是可扩展维度。分离避免范围爆炸 + 让授权门挂在已落地的 registry 上（registry 是授权门的天然挂载点，下一 change 受益）。

## Risks / Trade-offs

- [registry 函数访问 handler 实例属性] → D2 的 `handler` 参数解决；design 阶段确认 hermes 迁移后 `self.cwd`/`self._pending_briefs`/`self._get_anchor_prompt` 访问点全部改 `handler.*`。
- [双轨优先级歧义] → method 优先硬规定；`do_echo` 测试覆盖「同名时 method 胜出」。
- [net line count ≤0 约束] → utils 抽离是纯移动（净 0）；registry + dispatch fallback 是新增（小）；hermes 迁移是移动（净 0）。整体目标 ≤0，design 阶段核算。
- [与 `add-first-class-subagents` 撞车] → hermes 迁移不碰 subagents 的 `do_task`/`do_submit_result`/`_subagent_mgr`；双轨 method 优先保护其 in-handler 方法。
- [MAP.md stale] → 抽离后更新 MAP.md 的 ga.py 条目 + 新增 ga_utils.py/registry 模块条目。

## Migration Plan

1. 加 `register_tool` registry + `BaseHandler.dispatch` fallback 分支（additive，现有 do_* 不动）。
2. 加 dispatch 路径测试（`do_echo` 自注册 → 派发成功；method 优先；`_index`/`_tool_num` 注入）。
3. 抽 utils → `ga_utils.py`（纯移动，ga.py `from ga_utils import *`）。
4. 迁 hermes `do_skill_manage` + 3 helpers → 独立模块经 registry 自注册；跑 38 单测确认绿。
5. 更新 MAP.md。
- *回滚*：每步独立 commit；registry fallback 在 method 命中时不触发，回滚 step 1 即恢复纯 method 派发。

## Open Questions

- registry 函数访问 handler 实例属性的完整清单（hermes 迁移暴露面）——design 阶段深挖。
- `_TOOL_REGISTRY` 放哪个模块（`agent_loop.py` 内 vs 独立 `tool_registry.py`）——design 阶段定。
- `do_skill_manage` 迁出后，`ga.py` 是否还需 `import` skill_loader 的 6 个函数（迁到 hermes 模块内 import）——design 阶段定。
