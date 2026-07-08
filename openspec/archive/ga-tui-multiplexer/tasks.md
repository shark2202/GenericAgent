## 1. TUI 骨架与 Core 连接

- [x] 1.1 引入 ratatui + crossterm，搭建 `ga tui` 入口与主事件循环
- [x] 1.2 实现连 Item 1 Core daemon 的本地 IPC 客户端（订阅 session/project/goal 事件流 + 审批通道）
- [x] 1.3 实现渲染节流（事件合并 + 帧率限制 + 虚拟滚动）防高频 output 卡顿

## 2. 多面板视图与状态

- [x] 2.1 实现每 runner 一 pane 的布局（水平/垂直分割、切换、聚焦、关闭面板）
- [x] 2.2 面板状态渲染 `blocked / working / done`（来自 Core 事件流）
- [x] 2.3 关闭面板仅停止渲染订阅，不杀 runner（验证可 reattach）

## 3. 键鼠交互

- [x] 3.1 实现 tmux 式 prefix 键 + 面板索引切换/导航（prefix 可配置）
- [x] 3.2 实现鼠标点击聚焦 / 拖拽分割 / 滚动（含全局 mouse-off 开关）
- [ ] 3.3 vim 风格键位预设（可选切换）— _未验证_

## 4. 主题

- [x] 4.1 定义主题字段集（颜色 / 样式 / 状态色 blocked-working-done）
- [x] 4.2 内置主题 + 用户主题加载（`~/.config/ga/themes/`）
- [x] 4.3 运行时切换主题

## 5. 工具时间线 + 审批渲染

- [x] 5.1 结构化 runner（GA）渲染 tool_call args/result/timing 内联时间线
- [x] 5.2 Core 推送的审批在面板内提示，键鼠批准/拒绝（带 origin 三元组）
- [x] 5.3 审批决策经 IPC 回 Core（复用 Item 1 审批 API）

## 6. detach / reattach

- [x] 6.1 TUI 客户端无状态化：退出不影响 Core/runner
- [x] 6.2 reattach 从 SQLite + 实时流重建面板状态
- [ ] 6.3 验证关闭→重开后所有 running session 重现 — _无E2E验证_

## 7. agent-agnostic 面板适配

- [x] 7.1 按 runner kind 选渲染器（结构化时间线 vs 流 output+status）
- [x] 7.2 流 runner（opencode/claude-code/codex）面板渲染 output 行 + status
- [ ] 7.3 验证异构 runner 同屏（GA + codex 各自正确渲染）— _无E2E验证_

## 8. 集成验证

- [ ] 8.1 端到端：`ga tui` → 多面板 → 新建会话 → 工具时间线实时 → detach/reattach
- [ ] 8.2 端到端：TUI 审批闭环（批准/拒绝经 Core 生效）
- [ ] 8.3 端到端：Goal 只读视图展示（复用 Item 2 状态）
- [ ] 8.4 三平台终端冒烟（Linux/macOS/Windows ConPTY）
- [ ] 8.5 替换 `frontends/tuiapp_v2`：旧 `ga_cli` 的 `tui`/`tui2` 指向新 TUI
