# GenericAgent 项目地图 (MAP)

> 最后更新: 2026-07-08

---

## 顶层入口

| 文件 | 作用 |
|---|---|
| `agentmain.py` | **统一引擎入口**。SDK 类 `GenericAgent`，所有 16 个前端均 `from agentmain import GenericAgent`。含 4 种运行模式 (CLI/--task/--func/--reflect) |
| `agent_loop.py` | **纯引擎循环**，无 GA 业务。生成器 `agent_runner_loop()`：LLM 生成→工具 dispatch→StepOutcome 判定 |
| `ga.py` | **工具实现**。`GenericAgentHandler(BaseHandler)`：handler 方法（`do_*`）；module-level utils 已抽至 `ga_utils.py`（经 named import + `__all__` re-export，`from ga import smart_format` 等 back-compat 不破） |
| `ga_utils.py` | **工具函数库**（从 ga.py 抽离）。filetools/exectools/webtools/misc 分组的纯/半纯函数 + `script_dir`/`driver`/`_read_dirs` 全局 |
| `llmcore.py` | **LLM 通信层**。Session 体系：`ClaudeSession`/`LLMSession` (传统) + `NativeClaudeSession`/`NativeOAISession` (原生 tool) + `MixinSession` (故障转移) |
| `simphtml.py` | **HTML 简化**，将被访问页面的 DOM 转为 token 高效的文本 |
| `TMWebDriver.py` | **浏览器远程控制**。`TMWebDriver` 类提供 Chrome CDP 的 WebSocket 代理 |
| `mykey.py` | **(用户创建) 模型配置**。API key、session 类型、推理参数。模板: `mykey_template.py` |
| `ga.cmd` | Windows 批处理快捷入口 |
| `hub.pyw` | **服务启动器** (tkinter GUI)，管理所有 reflect 服务和 frontend 的启停 |
| `launch.pyw` | **桌面 GUI 入口** (pywebview + Streamlit)，嵌入 stapp.py 的 Web 前端 |
| `README.md` | 项目总览、设计哲学、快速开始 |

---

## 目录结构总览

```
GenericAgent/
├── agent_loop.py          ← 引擎：LLM↔工具循环 (纯框架，无业务)
├── agentmain.py           ← SDK：GenericAgent 类 + 4 种运行模式入口
├── ga.py                  ← 工具：do_*() 方法集合
├── ga_utils.py            ← 工具函数库（filetools/exectools/webtools/misc，从 ga.py 抽离）
├── llmcore.py             ← LLM：多协议 Session 体系
├── simphtml.py            ← HTML：页面 DOM 精简
├── TMWebDriver.py         ← 浏览器：CDP 代理控制
├── skill_loader.py        ← 技能发现：get_skill_catalog() + MCP 工具拼接
├── mcp_client.py          ← MCP Client：MCPClientManager 单例 (stdio/SSE)
├── ga.cmd                 ← 快捷启动 (仅 Windows)
├── hub.pyw                ← 服务管理器 (tkinter)
├── launch.pyw             ← 桌面 GUI (pywebview+Streamlit)
│
├── ga_cli/                ← 1. CLI 入口 (Python 包)
├── frontends/             ← 2. 前端层 (16+ 前端)
├── reflect/               ← 3. 自动化脚本层
├── plugins/               ← 4. 插件层 (hooks + 可选插件)
├── memory/                ← 5. 记忆/技能层 (L1-L4)
├── assets/                ← 6. 静态资源层
├── scripts/               ← 7. 构建/分发脚本
├── dist/                  ← 8. 分发产物
├── docs/                  ← 9. 文档
└── .github/workflows/     ← 10. CI/CD
```

---

## 1. ga_cli/ — CLI 入口 (Python 包)

| 文件 | 作用 |
|---|---|
| `__init__.py` | 包初始化 |
| `__main__.py` | `python -m ga_cli` 入口，调 `cli.main()` |
| `cli.py` | 多子命令 CLI：`ga list` (列出前端/reflect)、`ga status` (状态)、`ga update` (更新) |
| `mcp_cli.py` | MCP 子命令族：`ga mcp list/start/stop/restart`，管理 MCP Server 生命周期 |
| `ga_cli.cmd` | Windows `ga` 命令映射 |
| `ga-cli-install.cmd` | 一键安装 CLI (`pip install -e .`) |

---

## 2. frontends/ — 前端层 (所有交互入口)

### TUI (终端界面)

| 文件 | 技术栈 | 说明 |
|---|---|---|
| `tui_v3.py` | prompt_toolkit + rich | **推荐 TUI**，scrollback-first，6400 行单文件，功能最完整 |
| `tuiapp_v2.py` | Textual | V2 TUI |
| `tuiapp.py` | Textual | V1 TUI (旧版) |

### GUI (图形界面)

| 文件 | 技术栈 | 说明 |
|---|---|---|
| `qtapp.py` | PySide6 | Qt 桌面应用，聊天面板 + 悬浮按钮 |
| `desktop_pet_v2.pyw` | PIL + HTTP Server | 桌面宠物，支持皮肤系统 (skins/) |
| `desktop/` | Tauri | Tauri 桌面应用 (src-tauri/) |

### Web 前端

| 文件 | 技术栈 | 说明 |
|---|---|---|
| `stapp.py` | Streamlit | Web 聊天 UI |
| `stapp2.py` | Streamlit | Web 聊天 UI v2 |
| `conductor.py` | FastAPI + WebSocket | 多会话指挥器，提供 WebSocket API 和 conductor.html |

### IM Bot 前端 (均继承 `chatapp_common.AgentChatMixin`)

| 文件 | 平台 | 依赖 |
|---|---|---|
| `tgapp.py` | Telegram | python-telegram-bot |
| `dcapp.py` | Discord | discord.py |
| `qqapp.py` | QQ | qq-botpy |
| `wechatapp.py` | 微信 | itchat |
| `fsapp.py` | 飞书 (Lark) | lark-oapi |
| `wecomapp.py` | 企业微信 | wecom-aibot-sdk |
| `dingtalkapp.py` | 钉钉 | dingtalk-stream |

### 前端公共模块

| 文件 | 作用 |
|---|---|
| `chatapp_common.py` | IM Bot 基类 `AgentChatMixin`，处理消息收发、markdown 格式化、单实例锁 |
| `slash_cmds.py` | 斜杠命令解析：`/update`, `/autorun`, `/morphling`, `/goal`, `/hive`, `/conductor` 等 |
| `conductor_im_plugins/` | Conductor 的 IM 插件模板 (`_TEMPLATE.py`, `_email_example.py`, `_lark_example.py`) |
| `desktop_bridge.py` | 桌面桥接 |
| `genericagent_acp_bridge.py` | ACP 协议桥接 |
| `session_names.py` | 会话命名 |
| `plan_state.py` | 计划状态管理 |
| `cost_tracker.py` | 费用追踪 |
| `frontends/skins/` | 桌面宠物皮肤 (boy/dinosaur/doux/glube/line/mort/tard/vita) |

---

## 3. reflect/ — 自动化脚本层 (--reflect 模式)

所有脚本通过 `agentmain.py --reflect reflect/<name>.py` 运行。标准接口：`check() → task|None`, `INTERVAL`(轮询间隔), `ONCE`(单次), `on_done(result)`(可选回调)。

| 文件 | INTERVAL | ONCE | 说明 |
|---|---|---|---|
| `goal_mode.py` | - | - | 目标模式生产/消费循环 |
| `autonomous.py` | - | - | 自主运行模式 |
| `agent_team_worker.py` | - | - | Agent 团队协作 worker |
| `checklist_master.py` | - | - | 清单主控 |
| `scheduler.py` | 120s | No | 定时任务调度器，读写 `sche_tasks/` 目录 |

---

## 4. plugins/ — 插件层

| 文件 | 作用 |
|---|---|
| `hooks.py` | 事件钩子系统：`register(event)` + `trigger(event, ctx)`。agent_loop 在关键点调用 hook (tool_before/after, turn_before/after, llm_before/after, agent_before/after) |
| `project_mode.py` | 项目模式插件 |
| `langfuse_tracing.py` | Langfuse 追踪集成 |

---

## 5. memory/ — 记忆/技能层 (L1-L4)

### 层级架构

| 层级 | 文件 | 说明 |
|---|---|---|
| L1 | `global_mem_insight.txt` | 索引 (≤30 行)，路由到 L3 技能文件 |
| L2 | `global_mem.txt` | 长期事实记忆 |
| L3 | `memory/*.md` \| `*.py` | 任务 SOP / 技能文件 (按需读写) |
| L4 | `L4_raw_sessions/` | 原始会话历史 (供压缩/挖掘) |

### L3 技能文件 (workflow SOPs)

| 文件 | 说明 |
|---|---|
| `autonomous_operation_sop.md` | 自主运行 SOP (含子目录 helper.py, task_planning.md) |
| `goal_mode_sop.md` | 目标模式 SOP |
| `goal_hive_sop.md` | 目标蜂群 SOP |
| `plan_sop.md` | 计划制定 SOP |
| `review_sop.md` | 代码审查 SOP (含子目录 review_inline_prompt.txt) |
| `verify_sop.md` | 验证 SOP |
| `checklist_sop.md` | 清单 SOP |
| `incubator_sop.md` | 孵化器 SOP |
| `supervisor_sop.md` | 监督器 SOP |
| `morphling_sop.md` | 能力吸收 (将外部项目转为 Skill) |
| `project_mode_sop.md` | 项目模式 SOP |
| `scheduled_task_sop.md` | 定时任务 SOP |
| `memory_management_sop.md` | 记忆管理 SOP (L0 元规则) |
| `memory_cleanup_sop.md` | 记忆清理 SOP |
| `code_review_principles.md` | 代码审查原则 |
| `github_contribution_sop.md` | GitHub 贡献 SOP |
| `subagent.md` | 子代理说明 |
| `tmwebdriver_sop.md` | 浏览器操作 SOP |
| `web_setup_sop.md` | Web 配置 SOP |
| `computer_use.md` | 计算机操作说明 |
| `ultraplan_sop.md` | 深度计划 SOP |
| `vision_sop.md` | 视觉识别 SOP |
| `vue3_component_sop.md` | Vue3 组件 SOP |
| `procmem_scanner_sop.md` | 进程内存扫描 SOP |
| `ljqCtrl_sop.md` | 硬件控制 SOP |
| `mcp_sop.md` | MCP Client SOP (配置、mcp_call、热更新、故障恢复、记忆融合) |

### L3 可执行工具

| 文件 | 说明 |
|---|---|
| `ui_detect.py` | UI 检测工具 (YOLO daemon) |
| `procmem_scanner.py` | 进程内存扫描器 |
| `ljqCtrl.py` / `ljqCtrlBg.py` / `macljqCtrl.py` | 硬件控制工具 |
| `ocr_utils.py` | OCR 工具 |
| `checklist_helper.py` | 清单元数据管理 |
| `adb_ui.py` | ADB UI 操作 |
| `keychain.py` | 密钥管理 |
| `vision_api.template.py` | 视觉 API 模板 |

---

## 6. assets/ — 静态资源

| 文件 | 作用 |
|---|---|
| `sys_prompt.txt` / `sys_prompt_en.txt` | 系统提示词 (中/英) |
| `tools_schema.json` / `tools_schema_cn.json` | 工具 JSON Schema (供 LLM function calling) |
| `insight_fixed_structure.txt` / `_en.txt` | L1 记忆索引模板 |
| `global_mem_insight_template.txt` / `_en.txt` | L1 初始化模板 |
| `code_run_header.py` | Python 代码执行头 (注入 import 等) |
| `ga_httpapp.py` | HTTP API 骨架 (bottle) |
| `ga_ultraplan.py` | 深度规划模块 |
| `ga_install.ps1` / `ga_install.sh` | 安装脚本 (Windows/Linux) |
| `configure_mykey.py` | mykey 配置向导 |
| `agent_bbs.py` | Agent BBS (公告板) |
| `supergrok_proxy.py` | SuperGrok 代理 |
| `GenericAgent_Technical_Report.pdf` | 技术报告 PDF |
| `images/` | 图片资源 (logo, 截图, 表情) |
| `demo/` | 演示 GIF/PNG |
| `tmwd_cdp_bridge/` | Chrome 扩展 (CDP 桥接，含 background.js/content.js/manifest.json) |

---

## 7. scripts/ — 构建/分发

| 文件 | 作用 |
|---|---|
| `bundle-python.sh` | 构建自包含 CPython 运行时 (python-build-standalone, Python 3.11.15, 4 架构) → `dist/python-bundle/<arch>/python/` |
| `assemble-dist-local.sh` | 本地组装分发包 |

---

## 8. dist/ — 分发产物

| 路径 | 说明 |
|---|---|
| `python-bundle/win-x64/` | Windows 自包含 Python 运行时 |
| `release/` | Release 打包输出 |

---

## 9. docs/ — 文档

| 文件 | 说明 |
|---|---|
| `GETTING_STARTED.md` | 入门指南 |
| `installation.md` / `installation_zh.md` | 安装文档 |
| `macos_desktop_installation_zh.md` | macOS 桌面安装 |
| `SETUP_FEISHU.md` | 飞书配置 |
| `plans/` | 设计与实施计划 (daemon 相关) |

---

## 10. CI/CD

| 文件 | 说明 |
|---|---|
| `.github/workflows/release.yml` | Tag 触发 Release：4 架构交叉构建 (win-x64/mac-arm64/mac-x64/linux-x64)，产出便携分发包 |
| `pyproject.toml` | 项目元数据 + 依赖声明 (core / ui / all-frontends) |

---

## 核心数据流

```
┌──────────────┐    put_task()     ┌──────────────────┐
│   Frontend   │ ────────────────→ │  GenericAgent.run │
│  (任意 16 个) │ ←── display_queue │  (daemon 线程)     │
└──────────────┘                  └────────┬─────────┘
                                           │ generator
                                           ▼
                              ┌─────────────────────────┐
                              │   agent_runner_loop()   │
                              │  agent_loop.py          │
                              │  ┌───────────────────┐  │
                              │  │ llmclient.chat()   │  │  ← llmcore.py
                              │  │ (history in backend│  │
                              │  └───────┬───────────┘  │
                              │          │ tool_calls    │
                              │          ▼               │
                              │  ┌───────────────────┐  │
                              │  │ handler.dispatch() │  │  ← ga.py
                              │  │ → StepOutcome      │  │
                              │  └───────────────────┘  │
                              └─────────────────────────┘
```

## 关键设计原则

1. **单一 SDK 入口**: 所有前端通过 `GenericAgent.put_task()` → `display_queue` 消费
2. **生成器桥接**: `agent_runner_loop` 是 generator，`run()` 将其转为线程安全队列
3. **历史在 Session**: 完整对话历史存在 `llmclient.backend`，不传 messages 数组
4. **StepOutcome 控制流**: 工具不直接控制循环，只返回 outcome (next_prompt/should_exit/data)
5. **自进化记忆**: 任务完成后 Agent 自发调用 `start_long_term_update` 将经验结晶为 L3 Skill
6. **零依赖快速启动**: `agent_loop.py` + `agentmain.py` + `ga.py` + `ga_utils.py` + `llmcore.py` 构成最小可运行内核
7. **MCP 工具扩展**: 通过 `mcp_client.py` 连接外部 MCP Server，工具列表经 `get_skill_catalog()` 注入 system prompt（与 Skill 索引同级），`mcp_call` 统一调用，配置热更新 + 自动重连，单 Server 故障隔离
