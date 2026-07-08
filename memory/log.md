# Memory 目录更新日志

## 2026-07-08

* **Creation**: 采纳 OKF v0.1 格式规范，为 `memory/` 下全部 27 个 `.md` 文件添加 YAML frontmatter（type/title/description/tags/timestamp）。`type` 采用 3 类分类法：SOP（操作流程）/ Principle（原则红线）/ Duty（角色职责）。
* **Update**: 添加稀疏交叉链接——`goal_hive_sop` → `goal_hive_master_duty` + `goal_mode_sop`、`memory_cleanup_sop` → `memory_management_sop`、`computer_use` → `ljqCtrl_sop` + `vision_sop`、`tmwebdriver_sop` → `vision_sop`。
* **Creation**: 新增 `index.md` 目录索引（progressive disclosure，按 type 分组）。
* **Creation**: 新增 `log.md` 更新历史。
* **Update**: `mcp_sop.md`、`ultraplan_sop.md` 补入 frontmatter（初始迁移时遗漏）。
* **Note**: `review_sop/*.txt` 和 `L4_raw_sessions/*` 不纳入 OKF 范围（前者非 .md，后者是会话归档）。
