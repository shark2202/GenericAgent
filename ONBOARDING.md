# ONBOARDING — GenericAgent 新 Owner 上手手册

> 极简自进化自治 agent 框架（~3K 核心行 / 9 原子工具 / ~100 行 `agent_loop`）。
> 本手册填补既有文档的**运行/在建/避坑**空白，不重复既有内容。
> 交叉引用（先读这三个）：
> - **`MAP.md`** — 顶层入口、目录结构、核心数据流、设计原则（看"项目长什么样"）
> - **`AGENTS.md`** — 各子项目的验证/构建命令清单（看"改完代码跑什么"）
> - **`docs/onboarding-engine-reading-guide.md`** — 核心引擎行号级阅读指南 + 已验证 `--func` 冒烟链路（看"代码怎么读"）

---

## 一、分层 Runbook（怎么跑 / 构建 / 测试）

> 约定：下文 `python` 指仓库 `.venv` 的 Python（`D:\GenericAgent\.venv\Scripts\python.exe`，版本 3.11.15）。
> 首次准备：`uv pip install -e ".[ui]"`（装核心 + UI 依赖）。`pytest`/`ruff`/`mcp` 不在任何 extra 内，按需单独装（见 §三 Gotchas）。
> 所有命令已在本机 Windows 实测通过（2026-07-19）。

### 0. 引擎冒烟（零风险，验证 key + 链路）

| 项 | 命令 | 期望输出 |
|---|---|---|
| 验 key 加载 | `python -m agentmain --list-llms` | `[Info] Load mykeys from D:\GenericAgent\mykey.jsonc` + `Available LLM models:` + `[0] NativeOAISession/openai ← current` |
| 单轮端到端 | `python -m agentmain --func temp\smoke_prompt.txt --nobg` | exit 0；写 `temp\smoke_prompt.out.txt`，内含 `SMOKE_OK` + `[ROUND END]`；stdout 见 `sys_prompt 4459 chars`、`tokens=6` |
| 看结果 | `type temp\smoke_prompt.out.txt` | `LLM Running (Turn 1) ... SMOKE_OK ... [ROUND END]` |

- **报错处理**：`--func` 不加 `--nobg` 会 self-spawn 后台进程，冒烟必须带 `--nobg`。
- 冒烟链路的行号级拆解见 `docs/onboarding-engine-reading-guide.md`（mykey→llmcore→agentmain→agent_loop→ga.do_no_tool）。

### 1. TUI（终端界面，`frontends/tui_v3.py`）

| 项 | 命令 | 期望 |
|---|---|---|
| 启动（推荐 TUI） | `python frontends\tui_v3.py` | 进入 prompt_toolkit + rich 交互界面 |
| 经 ga 入口 | `ga tui` | 同上 |

- **报错/不稳**：在 **PowerShell / cmd 下 TUI 抖动/渲染异常** → 改用 **Git Bash** 运行；必要时 `uv pip install -U prompt_toolkit rich`。
- 语法验证（不启动）：`python -c "import ast; ast.parse(open('frontends/tui_v3.py',encoding='utf-8').read())"` → 无输出即 OK。

### 2. Streamlit Web 前端（`frontends/stapp.py` / `launch.pyw`）

| 项 | 命令 | 期望 |
|---|---|---|
| 启动 Web | `streamlit run frontends\stapp.py` | 起本地 server，浏览器开聊天 UI |
| 桌面壳（webview+streamlit） | `python launch.pyw` | pywebview 嵌入 stapp 的桌面窗口 |
| 经 ga 入口 | `ga web` | Web 增强版；`ga web --native` = 基础版桌面壳 |

- **报错**：`ModuleNotFoundError: pywebview` → `uv pip install pywebview`（在 `[ui]` extra 内，装过 `[ui]` 就有）。

### 3. IM Bot 前端（tg/dc/qq/wechat/fs/wecom/dingtalk）

所有 IM bot 继承 `frontends/chatapp_common.py:AgentChatMixin`，依赖在 `pyproject.toml` 的 `[all-frontends]` extra。未装对应 SDK 时**友好报错**而非崩溃。

| 平台 | 文件 | 启动 | 缺依赖时的输出 |
|---|---|---|---|
| Telegram | `frontends/tgapp.py` | `python frontends\tgapp.py` | `Please ask the agent install python-telegram-bot to use telegram module.` |
| Discord | `frontends/dcapp.py` | `python frontends\dcapp.py` | 同型提示（`discord.py`） |
| QQ | `frontends/qqapp.py` | `python frontends\qqapp.py` | `qq-botpy` |
| 微信 | `frontends/wechatapp.py` | `python frontends\wechatapp.py` | `itchat` |
| 飞书 | `frontends/fsapp.py` | `python frontends\fsapp.py` | `lark-oapi` |
| 企业微信 | `frontends/wecomapp.py` | `python frontends\wecomapp.py` | `wecom-aibot-sdk` |
| 钉钉 | `frontends/dingtalkapp.py` | `python frontends\dingtalkapp.py` | `dingtalk-stream` |

- **装全部 bot 依赖**：`uv pip install -e ".[all-frontends]"`（按需，勿盲目全装）。
- **报错**：bot 还需各自平台的 token/凭证（见 `mykey.jsonc` / `docs/SETUP_FEISHU.md`）。缺 token 会在连接阶段失败。
- 语法批量验证：`python -c "import ast,glob;[ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('frontends/*app.py')]"`。

### 4. reflect 脚本（`agentmain.py --reflect reflect/<name>.py`）

reflect 模式 = `--reflect` 加载监控脚本，标准接口 `check() → task|None`、`INTERVAL`（轮询间隔）、`ONCE`（单次）、`on_done(result)`（可选回调）。脚本是**长驻循环**，沙箱内勿直接前台跑。

| 脚本 | 启动 | 说明 |
|---|---|---|
| `reflect/scheduler.py` | `python -m agentmain --reflect reflect\scheduler.py` | 定时任务调度器（INTERVAL=120s），读写 `sche_tasks/` |
| `reflect/goal_mode.py` | `python -m agentmain --reflect reflect\goal_mode.py` | 目标模式生产/消费循环（配合 `/goal` 斜杠命令） |
| `reflect/autonomous.py` | `python -m agentmain --reflect reflect\autonomous.py` | 自主运行模式 |

- **服务管理器**：`python hub.pyw`（tkinter GUI）统一管理所有 reflect 服务和 frontend 的启停。
- **报错**：reflect 脚本跑不起来 → 先 `python -m agentmain --help` 确认 `--reflect` 参数存在；脚本须实现 `check()`。
- 相关 SOP：`memory/goal_mode_sop.md`、`memory/autonomous_operation_sop.md`、`memory/scheduled_task_sop.md`。

### 5. Tauri 桌面前端（`frontends/desktop/`）

| 项 | 命令（在 `frontends\desktop\src-tauri\` 下） | 期望 |
|---|---|---|
| 构建检查 | `cargo check` | `Finished `dev` profile ... in Ns`，0 error |
| 完整构建 | `cargo build` | 产出桌面应用 |

- **报错**：缺 `tauri-cli` → `cargo install tauri-cli`；前端部分在 `frontends/desktop/package.json`（Node 侧）。

### 6. Rust CLI / TUI（`ga_rust_cli/` · `ga_rust_tui/`）

| 项 | 命令 | 期望 |
|---|---|---|
| CLI 构建检查 | `cd ga_rust_cli && cargo check --bin ga` | `Finished`，0 error（仅 unused-import/variable 警告） |
| TUI 构建检查 | `cd ga_rust_tui && cargo check` | `Finished`，0 error |
| CLI 测试 | `cd ga_rust_cli && cargo test` | （Rust 测试套件） |

- **注意**：`ga_rust_cli/_check_errors.txt`、`build_errors*.txt` 是**旧 worktree**（`temp/e2e_worktrees/ga-core-cli/ga_rust`）的残留，记录了 5 个 E0599 error——**不代表主 checkout 状态**。主树 `cargo check --bin ga` 实测 0 error。

### 7. hermes 自进化（`plugins/skill_evolution.py`）

hermes = 任务完成后把经验结晶成 Skill（`.agents/skills/<name>/SKILL.md`）的 post-task 蒸馏回路。v1 feature-complete。

| 项 | 命令 | 期望 |
|---|---|---|
| 单元测试 | `python -m pytest tests\test_skill_evolution.py tests\test_skill_evolution_plugin.py -q` | 全绿（38 tests） |
| 触发开关 | 设环境变量 `GA_SKILL_EVOLUTION_ENABLED=1` 后正常跑任务 | 任务完成（`agent_after` 钩子，turn≥6）时后台 spawn `distill()` |
| 语法验证 | `python -c "import ast;ast.parse(open('plugins/skill_evolution.py',encoding='utf-8').read())"` | 无输出即 OK |

- v1 入口：`ga.py:do_start_long_term_update` → `plugins/skill_evolution.py:distill()`（一次性自包含 LLM 调用）→ `_apply_op` → `do_skill_manage` 落盘。
- **不默认开启**：须显式 `GA_SKILL_EVOLUTION_ENABLED=1`。开关关闭时 hook 静默返回。
- 在建/风险见 §二。

### 8. plugins/hooks（事件钩子）

`plugins/hooks.py`：`register(event)` + `trigger(event, ctx)`。`agent_loop.py` 在 8 个点调钩子：tool_before/after、turn_before/after、llm_before/after、agent_before/after。hermes 自进化挂在 `agent_after`。启动期 `agentmain.py:12` 调 `discover_and_load()` 自动加载 `plugins/*.py`。

- 验证加载：`python -m agentmain --list-llms` 时若插件 import 失败会在 stderr 报 `[Plugin]` 警告。
- 文档：`docs/plugins.md`。

### 9. MCP（`mcp_client.py` + `ga mcp`）

`mcp_client.py` 的 `MCPClientManager` 单例连接外部 MCP Server（stdio/SSE），工具经 `get_skill_catalog()` 注入 sys_prompt，统一用 `mcp_call` 调，配置热更新 + 自动重连，单 Server 故障隔离。

| 项 | 命令 | 期望 |
|---|---|---|
| 列已连 server | `ga mcp list` | 示例：`codebase-memory [stdio] connected tools=14` |
| 启/停/重启 | `ga mcp start` / `ga mcp stop` / `ga mcp restart` | 管理 MCP Server 生命周期 |
| MCP 单元测试 | `python -m pytest tests\test_mcp_client.py tests\test_mcp_e2e.py -q` | 须先 `uv pip install mcp`，否则 5 fail |

- **报错**：`No module named 'mcp'` → `uv pip install mcp`（不在任何 extra；见 §三）。
- SOP：`memory/mcp_sop.md`。

---

## 二、当前在建工作 + 风险 + Blocker

> 以下声称均已对照源码行号 / `HANDOFF.md` / `§8_progress.md` 核实（2026-07-19）。

### 1. hermes v1.5（未开始；Tier 0 是真 bug，优先做）

v1.5 计划见 `hermes/NOTES_v1.5_recommendations.md`（R1–R9）。**Tier 0 四项已逐条对照 `plugins/skill_evolution.py` 源码核实**：

| 项 | 源码核实结果 | 风险 |
|---|---|---|
| **R6 breaker 不 reset** | VERIFIED：`_auto_patch_counts`（`:37`）只在 `:106` increment、`:88` check（≥5 触发熔断），**全文件无 reset 代码**；`:36` 注释"前台修正时重置"不实。→ 连续 5 次自动 patch 后技能**永久熔断锁死**直至进程重启 | 真 bug，非设计建议 |
| **R7 dead-counter** | VERIFIED：`_consecutive_auto_distills`（`:38`，`:78` global，`:107` increment，`:110` reset）**从未被任何 check 读取**；`MAX_CHANGES_PER_DISTILL=3`（`:31`）同样休眠 | 死代码，须 wire 或删 |
| **R8 fitness-log 缺失** | VERIFIED：`distill`/`_apply_op`/`do_skill_manage` 均不写 `memory/skill_exp_<name>.md`。该文件仅被 `skill_loader.py:297` 读取（驱动 L1 hot/cold），由 Agent 通用 `file_patch` 路径写。spec §7 要求的结构化 `(ts,action,source_turns,signal)` 蒸馏日志**未实现** | R5 ratchet 缺数据基底 |
| **R9 validate loadability** | `validate_skill`（`skill_loader.py`）仅查 frontmatter + heading，不调 `get_skill_detail` 确认可加载 | patch 破坏 loader 解析仍被保留 |

v1.5 Tier 1：**R1**（接 `_on_tool_after`/`_on_turn_after` 负信号钩子，现 `:184-198` 是 `return` no-op）、**R4**（frontmatter 加 `boundaries` 字段 + `## 适用边界` 段）。**Tier 2/v2**：R2 独立 scorer、R3 test-prompts、R5 ratchet。

### 2. §8 审批系统（ga_rust_cli）

`§8_progress.md` 称 §8.1–8.4 已实现、§8.5（cargo 验证）待完成。**核实结果：`§8_progress.md` 已 STALE**。

- §8 代码在 `ga_rust_cli/src/core/`：`approval.rs`、`orchestrator.rs`、`commands.rs`、`store/`。
- **实测** `cd ga_rust_cli && cargo check --bin ga` → **exit 0，0 error**（仅 unused-import/variable 警告）。
- `§8_progress.md` 列的 5 个 E0599 error 来自 `ga_rust_cli/_check_errors.txt`，该文件生成自旧 worktree `temp/e2e_worktrees/ga-core-cli/ga_rust`，**非主 checkout**。即 §8.5 在主树实际已通过。
- 遗留：`approval_allow_tools` 方法是否存在、`Origin::Cli` 是否存在等细节，建议对照 `approval.rs` / `commands.rs` 实查。

### 3. 被阻塞的集成测试（hermes v1 DoD gap）

- **T1/T3/T8 集成测试**（`tests/test_skill_evolution_integration.py`）未补，因需 deps-complete env。
- **Blocker**：Linux 沙箱（`/mnt/d/GenericAgent`）`import ga.py` 失败——`ga.py` 依赖 `pywebview` 等 Windows-heavy deps，仓库 `.venv` 是 Windows-only。需在 Windows 上 `uv pip install -e ".[ui]"` 或 Linux 带 pywebview 的环境跑。
- 详情见 `HANDOFF.md` "Step 5b"。

### 4. 本地安装器打包计划（将废弃 release CI）

- 现状：推 `v*` tag 触发 `.github/workflows/release.yml`（4 架构交叉构建 + 自包含 CPython 运行时）。
- 计划：落地本地安装器方案后**停用** release CI，改用 `scripts/release-local.sh`、`scripts/build-installer.sh`。
- 设计文档：`docs/plans/2026-07-09-local-installer-packaging-design.md`（见 `AGENTS.md` Release 段）。

---

## 三、Gotchas（避坑）

1. **Windows TUI 不稳**：`frontends/tui_v3.py` 在 PowerShell/cmd 下渲染抖动/异常 → **用 Git Bash** 运行。
2. **禁 Python 3.14**：`pyproject.toml` 声明 `requires-python = ">=3.10,<3.14"`，3.14 与 `pywebview` 及部分依赖不兼容。仓库 `.venv` 用 3.11.15。
3. **mykey 已迁移 jsonc**：模型配置读 `mykey.jsonc`（不再 `mykey.py`）；加载链 `mykey.py → mykey.jsonc → mykey.json`（`llmcore._load_mykeys`）。模板：`mykey_template.jsonc`。
4. **分支策略**：主分支 `main`，集成分支 `dev`；打 `v*` tag 前先在 `dev` 完成 dev/test 验证。
5. **ruff 已知违规 + dev CI 未落地**：`ruff check .` 实测**报 2966 个历史违规**（764 可 `--fix`），但 pyproject 顶层 `select`/`ignore` 配置已 deprecated（ruff 0.15 警告）。新代码不得引入新违规；全仓 lint 转绿待 dev CI 落地前专项修复。
6. **pytest/ruff/mcp 不在 extra 中**：`uv pip install -e ".[ui]"` 不会装 `pytest`/`ruff`/`mcp`。按需：
   - `uv pip install pytest ruff`（跑测试/lint）
   - `uv pip install mcp`（跑 MCP 相关 5 个测试，否则 `No module named 'mcp'` + 4× stdio e2e connect fail）
7. **`ruff check .` exit 0 不代表无违规**：本环境 ruff 0.15.22 即便报 2966 error 也 exit 0（疑似 deprecated 配置副作用）。判断 lint 状态看输出行数，别信 exit code。
8. **`ga_rust_cli/_check_errors.txt` 等是旧 worktree 残留**：不代表主树编译状态，以 `cargo check` 实测为准。
9. **类型检查未配置**：项目无 mypy / pyright（见 `AGENTS.md` "暂未启用"）。
10. **CI 仅 release**：仅 tag 触发的 release CI；push/PR 触发的 dev/test/staging CI 尚未落地。

---

## 四、上手路径（推荐阅读/运行顺序）

### 第 1 步：建立心智模型（读，不跑）
1. `MAP.md` — 顶层入口表 + 目录结构 + 核心数据流图 + 7 条设计原则（~15 min）
2. `docs/onboarding-engine-reading-guide.md` — 引擎四文件行号级阅读（`agent_loop.py` → `agentmain.py` → `ga.py` → `llmcore.py`），含已验证 `--func` 冒烟链路（~20 min）

### 第 2 步：跑通冒烟（验证环境可用）
3. `python -m agentmain --list-llms`（验 key）
4. `python -m agentmain --func temp\smoke_prompt.txt --nobg`（验端到端，见 §一.0）

### 第 3 步：验证基线绿
5. `uv pip install pytest ruff mcp`
6. `python -m pytest tests\ -q` → 期望 `214 passed`
7. `python -m ruff check .` → 预期历史违规（新代码不引入新违规即可）
8. `cd ga_rust_cli && cargo check --bin ga` / `cd frontends\desktop\src-tauri && cargo check`（Rust 侧基线）

### 第 4 步：玩前端
9. `python frontends\tui_v3.py`（Git Bash 下）或 `ga web`（Streamlit）

### 第 5 步：接手在建工作
10. 读 `HANDOFF.md`（hermes v1/v1.5 状态 + Step 5b 阻塞）
11. 读 `hermes/NOTES_v1.5_recommendations.md`（R1–R9 优先级 + 行号）
12. 按 §二确认 §8 真实状态（`§8_progress.md` 已 stale，以 `cargo check` 实测为准）

> 不重复的内容：完整目录树见 `MAP.md`；构建/Release 命令见 `AGENTS.md`；引擎行号见 `onboarding-engine-reading-guide.md`。本手册只补运行/在建/避坑。
