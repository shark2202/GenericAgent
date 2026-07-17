## Why

ga.py 的 `GenericAgentHandler` 是 ~840 行 god-class，所有 Agent 工具（`code_run`/`file_*`/`web_*`/`skill_manage`/`mcp_call`…）都作为 `do_*` 方法寄生其上。hermes（未提交）和 `add-first-class-subagents`（build, 0/31）都在继续往这个类塞方法，未来工具只会更多。当前新增一个工具必须改 handler 类体 + ga.py 主文件，违反 `CONTRIBUTING.md`「new features add implementations, not modify old logic」。需要让工具能从独立模块自注册，不寄生 handler 类。

## What Changes

- 新增 additive `register_tool` 双轨 registry：`BaseHandler.dispatch` 先试 `getattr(self,"do_<name>")`（现有方法，零行为变更），再试 module-level `_TOOL_REGISTRY`（新工具自注册）。
- 把 hermes 的 `do_skill_manage` + 3 helpers（`_validate_skill_content`/`_set_frontmatter_flag`/`_build_skill_brief`）从 ga.py 迁到独立模块经 registry 自注册，作为首个 feature-tool 验证。
- 抽离 ga.py 的 ~15 个 module-level 纯工具函数到 `ga_utils.py`（`file_read`/`file_patch`/`code_run`/`web_*`/`ask_user`/`smart_format` 等），ga.py 只保留 handler 类 + 组合。
- 加 dispatch 路径测试（审计 F26：核心热路径零测试）。
- 不改现有 `do_*` 调用路径/行为（向后兼容，保护 `add-first-class-subagents` 不破）。

## Capabilities

### New Capabilities

- `tool-dispatch`: 工具注册与派发的可扩展机制——双轨派发（method-track `do_<name>` 优先，registry-track `register_tool` fallback），新工具经 `register_tool` 自注册不需改 handler 类体或 ga.py 主文件。

### Modified Capabilities

（无。现有 `do_*` 派发是约定式、未入 spec，本 change 不改其 requirement，只加 fallback 路径；`skill-discovery`/`skill-memory-integration` 的 requirement 不变，hermes `do_skill_manage` 迁移是实现细节非 spec 变更。）

## Impact

- **代码**：`agent_loop.py`（`BaseHandler.dispatch` +1 fallback 分支）、`ga.py`（抽 utils、迁 `do_skill_manage`）、新建 registry 模块 + `ga_utils.py`。
- **依赖**：无新增（`CONTRIBUTING.md` 约束）。
- **兼容性**：现有 `do_*` 零行为变更；`add-first-class-subagents` 的 in-handler `do_task`/`do_submit_result` 不受影响（双轨 method 优先）。
- **前置依赖**（非本 change task，build 前需解决）：3 个 stalled-complete changes（`add-llm-slash-cmd`/`skill-lazy-load-mtime-cache`/`fix-skill-loader-home-windows`）的 verify/archive + CI gates（审计 F13）。
- **测试**：新增 dispatch 路径测试（审计 F26）；hermes 38 单测须仍绿。
- **约束**：net line count ≤0（`CONTRIBUTING.md`）；小变更半径；let-it-crash（不新增裸 `except:pass`）；无新依赖；自文档最少注释。
