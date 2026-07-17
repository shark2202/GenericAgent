## 1. Registry core (additive, no behavior change)

- [x] 1.1 新建 `tool_registry` 模块：定义 `register_tool(name)` 装饰器 + module-level `_TOOL_REGISTRY` dict
- [x] 1.2 在 `BaseHandler.dispatch`（`agent_loop.py:18-29`）加 registry fallback 分支：method 未命中时查 `_TOOL_REGISTRY`，命中则调 `fn(handler, args, response)`；`_index`/`_tool_num` 注入对 registry 路径同样生效
- [x] 1.3 确认现有 `do_*` 走 method-track 零行为变更（手动回归：code_run/file_read/skill_manage 派发路径与 StepOutcome 形态不变）

## 2. Dispatch 路径测试（审计 F26）

- [x] 2.1 新增 `tests/test_agent_loop_dispatch.py`：mock client + canned responses + test handler（`do_echo` 方法）覆盖单 tool→done / MAX_TURNS / `should_exit` / `no_tool` / `_done_hooks` / `tool_results` 组装
- [x] 2.2 测试 registry 派发：独立模块 `register_tool("echo")(fn)`，不改 handler 类体，断言 LLM `echo` tool_call 派发成功并返回 `StepOutcome`
- [x] 2.3 测试 method 优先：同名时 `do_<name>` method 胜出，registry 不被调
- [x] 2.4 测试 `_index`/`_tool_num` 注入 parity：registry 函数收到的 args 含 `_index`/`_tool_num`，与 method 路径一致
- [x] 2.5 测试未知工具：无 method 无 registry → "未知工具" `StepOutcome`

## 3. utils 抽离 → `ga_utils.py`（纯移动）

- [ ] 3.1 新建 `ga_utils.py`，按分组移入 ~15 函数（filetools/exectools/webtools/misc）+ `script_dir`/`driver`/`_read_dirs` 全局
- [ ] 3.2 `ga.py` 改 `from ga_utils import *`（或 named import），删原 module-level 函数定义
- [ ] 3.3 `ruff check ga.py ga_utils.py` 0 新违规；`py_compile` 两文件；全单测仍绿
- [ ] 3.4 更新 `MAP.md`（ga.py 条目 + 新增 ga_utils.py）

## 4. hermes `do_skill_manage` 迁移到 registry 自注册

- [ ] 4.1 新建独立模块，把 `do_skill_manage` + `_validate_skill_content`/`_set_frontmatter_flag`/`_build_skill_brief` 迁入；`self.*` 改 `handler.*`（`cwd`/`_pending_briefs`/`_get_anchor_prompt`）；skill_loader 6 函数改本模块 import
- [ ] 4.2 经 `register_tool("skill_manage")` 自注册；从 `GenericAgentHandler` 类体删除 `do_skill_manage` + 3 helpers
- [ ] 4.3 跑 `tests/test_skill_evolution.py` + `tests/test_skill_evolution_plugin.py`（38 tests）确认绿——provenance gate/`.prev`/熔断行为不变
- [ ] 4.4 验证 `do_skill_manage` 经 registry 派发（method 已删，走 fallback）：T1 trigger / T3 patch / T8 Brief 路径（若 deps-complete env 可用）

## 5. 收尾

- [ ] 5.1 net line count 核算：ga.py 净减（utils 迁出 + `do_skill_manage` 迁出 − 新 import 行）≤0 或接近
- [ ] 5.2 `ruff check .` 不引入新违规；`pytest tests/` 不破现有用例
- [ ] 5.3 更新 `MAP.md` / `docs/architecture.md`（如分层有变：registry 模块定位）
