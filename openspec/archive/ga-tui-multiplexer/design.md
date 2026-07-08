## Context

Item 1（Core+CLI）与 Item 2（Goal）提供了编排后端 + agent-agnostic Runner + 事件流 + 审批。人类侧 TUI 仍是旧 `tuiapp_v2`（Python Textual，单会话）。herdr 证明单 Rust 二进制 + ratatui 可做 agent multiplexer TUI（多面板、detach/reattach、键鼠、主题）；superfile 证明 TUI 精致 UX（多面板/主题/快捷键）。本变更取 herdr 的 multiplexer 机制 + superfile 的精致，作为 Core 前端。

约束：Rust/ratatui（同 `ga` 二进制）；依赖 Item 1 Core IPC/事件流/审批 API；跨平台终端；v1 不含 socket API/插件；不含 Goal 控制器逻辑（只读展示）。

## Goals / Non-Goals

**Goals:**

- 多 runner 面板（blocked/working/done）一览
- detach/reattach
- 键鼠双支持
- 主题
- 工具时间线 + 审批渲染
- agent-agnostic 面板适配

**Non-Goals:**

- socket API / 插件
- 重做 Core/CLI/Goal
- 内嵌 agent 逻辑（TUI 纯前端）

## Decisions

### D1: TUI 库 = ratatui + crossterm

Rust 标准 TUI 栈，对齐 herdr；crossterm 跨平台（含 Windows ConPTY）。

- 替代：tui-rs（已弃用，否决）；cursive（非终端，否决）。

### D2: 面板模型 = 每 runner 一 pane

面板状态 blocked/working/done 来自 Core 事件流；支持水平/垂直分割、切换、聚焦、关闭面板（关闭仅停止渲染订阅，不杀 runner）。

- 为何：对齐 herdr 多 pane + superfile 多面板。

### D3: 键鼠双支持 = tmux prefix + 鼠标

默认 tmux 式 prefix 键（可配置）；鼠标点击聚焦/拖拽分割/滚动。两套并存，按场景选；鼠标可全局关闭。

- 为何：herdr 明确「keyboard and mouse, both first-class」。

### D4: 主题 = TOML 主题文件

颜色/样式/状态色（blocked/working/done）配置；内置几套 + 用户 `~/.config/ga/themes/`。

- 为何：对齐 superfile/herdr 主题机制。

### D5: 与 Core 通信 = 复用 Item 1 client IPC

TUI 是 Core 的一个前端客户端，经本地 socket 连 daemon，订阅事件流（session/project/goal 事件 + 审批）。渲染节流：事件合并 + 帧率限制（如 60fps / 自适应）+ 虚拟滚动，避免高频 output 行卡顿。

- 为何：Core 持状态，TUI 无状态；对齐 Galley「peer frontend」。

### D6: detach/reattach = 无状态渲染器

TUI 客户端退出不影响 Core/runner；reattach 重新连 daemon 订阅事件流，从 SQLite + 实时流重建面板状态。

- 为何：herdr detach/reattach + Galley 跨重启。

### D7: 审批 UI = Core 推送，面板内提示

Core 挂起的审批经 IPC 推到 TUI，聚焦面板或通知区提示，键鼠批准/拒绝（带 origin）。

- 为何：单一策略源在 Core，TUI 只管渲染/输入。

### D8: agent-agnostic 面板适配

结构化 runner（GA）：渲染 tool_call args/result/timing 时间线。流 runner（opencode/claude-code/codex）：渲染 output 行 + status。面板按 runner kind 选渲染器。

- 为何：B 方案 agent-agnostic 的必然结果。

## Risks / Trade-offs

- [高频 output 行致渲染卡顿] → 事件合并 + 帧率限制 + 虚拟滚动
- [ratatui 跨平台终端差异（Windows ConPTY）] → CI 三平台冒烟
- [主题格式碎片] → v1 固定字段集，扩展走后续
- [键鼠冲突] → prefix 键可配置 + 鼠标可全局关闭

## Open Questions

- 默认 prefix 键与 vim 风格键位预设
- 面板分割的最大数量/性能边界
- 主题字段集范围
- Goal 只读视图的展示粒度
