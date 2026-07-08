## Why

Item 1（Core + CLI）与 Item 2（Goal）提供了编排后端，但人类侧的 TUI 仍是旧的单会话 `tuiapp_v2`（Python Textual），缺乏 herdr 式「多 agent 面板一览、detach/reattach、键鼠双支持、主题、内联工具时间线/审批」体验。本变更构建 **Rust / ratatui 的 multiplexer TUI** 作为 Core 的前端：连 Core daemon，把多个 runner（GA / opencode / claude-code / codex）以面板形式一览（blocked / working / done），支持 detach/reattach、键鼠、主题，内联渲染工具时间线与审批。它替换 `tuiapp_v2`。

## What Changes

- **NEW**：Rust / ratatui TUI 客户端 —— 连 Item 1 Core daemon 的本地 IPC，订阅事件流，渲染多 runner 面板
- **NEW**：多面板视图 —— 每 runner 一个 pane，状态 `blocked / working / done` 一览；可分割 / 切换 / 聚焦
- **NEW**：detach / reattach —— TUI 客户端为无状态渲染器，detach 不影响 Core / runner，reattach 重新订阅事件流（Core 持状态）
- **NEW**：键鼠双支持 —— tmux 式 prefix 键 + 鼠标点击 / 拖拽 / 分割
- **NEW**：主题 —— 主题文件（颜色 / 样式），内置 + 用户自定义
- **NEW**：内联工具时间线 + 审批渲染 —— 结构化 runner（GA）渲染 tool_call args/result/timing；流 runner 渲染 output 行 + status；Core 挂起的审批推到面板提示批准 / 拒绝
- **NEW**：agent-agnostic 渲染 —— 面板按 runner kind 适配渲染（结构化 vs 流）
- **BREAKING**：替换 `frontends/tuiapp_v2`（Python Textual）；`ga` 默认或 `ga tui` 启动新 TUI

## Capabilities

### New Capabilities

- `tui-multiplexer`: Rust / ratatui 的 multiplexer TUI 前端 —— 多 runner 面板、detach/reattach、键鼠、主题、工具时间线 + 审批渲染、agent-agnostic 面板适配

### Modified Capabilities

<!-- 无现有 main spec 级需求变更（TUI 消费 Item 1 Core 的 IPC/事件流/审批 API，不改 Core spec） -->

## Impact

- 新增 Rust TUI 模块（同 `ga` 二进制，`ga tui` 或默认启动），依赖 ratatui + crossterm
- 替换 `frontends/tuiapp_v2`（Python Textual）；旧 `ga_cli` 的 `tui`/`tui2` 命令指向新 TUI
- 复用 Item 1：Core client↔daemon IPC、事件流订阅、审批 API、origin 三元组（TUI 发起的写操作带 origin）
- 复用 Item 2：goal 状态/进度可在 TUI 面板展示（只读视图）
- 跨平台终端（Linux/macOS/Windows ConPTY）
