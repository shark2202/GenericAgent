## 1. Frontmatter 迁移 — 根目录 SOP 文档

- [x] 1.1 `memory/autonomous_operation_sop.md` — 加 frontmatter（type: SOP, title, description, tags, timestamp）
- [x] 1.2 `memory/checklist_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.3 `memory/code_review_principles.md` — 加 frontmatter（type: Principle）
- [x] 1.4 `memory/computer_use.md` — 加 frontmatter（type: SOP）
- [x] 1.5 `memory/github_contribution_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.6 `memory/goal_hive_master_duty.md` — 加 frontmatter（type: Duty）
- [x] 1.7 `memory/goal_hive_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.8 `memory/goal_mode_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.9 `memory/incubator_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.10 `memory/ljqCtrl_sop.md` — 加 frontmatter（type: SOP, resource: ../memory/ljqCtrl.py）
- [x] 1.11 `memory/memory_cleanup_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.12 `memory/memory_management_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.13 `memory/morphling_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.14 `memory/plan_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.15 `memory/procmem_scanner_sop.md` — 加 frontmatter（type: SOP, resource: ../memory/procmem_scanner.py）
- [x] 1.16 `memory/project_mode_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.17 `memory/review_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.18 `memory/scheduled_task_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.19 `memory/subagent.md` — 加 frontmatter（type: SOP）
- [x] 1.20 `memory/supervisor_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.21 `memory/tmwebdriver_sop.md` — 加 frontmatter（type: SOP, resource: ../TMWebDriver.py）
- [x] 1.22 `memory/verify_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.23 `memory/vision_sop.md` — 加 frontmatter（type: SOP, resource: ../memory/vision_api.template.py）
- [x] 1.24 `memory/vue3_component_sop.md` — 加 frontmatter（type: SOP）
- [x] 1.25 `memory/web_setup_sop.md` — 加 frontmatter（type: SOP）

## 2. Frontmatter 迁移 — 子目录文档

- [x] 2.1 `memory/autonomous_operation_sop/task_planning.md` — 加 frontmatter（type: SOP）
- [x] 2.2 `memory/review_sop/review_inline_prompt.en.txt` → 评估是否转 `.md` + frontmatter（若是 .txt 不纳入 OKF，保持原样）
- [x] 2.3 `memory/review_sop/review_inline_prompt.txt` → 同上评估

## 3. 交叉链接

- [x] 3.1 逐个读 SOP 内容，识别强依赖关系，添加稀疏交叉链接（bundle-relative 绝对路径）
- [x] 3.2 重点链接：goal_hive_sop → goal_hive_master_duty、tmwebdriver_sop → vision_sop、memory_cleanup_sop → memory_management_sop 等已确认的强依赖

## 4. 目录索引与更新历史

- [x] 4.1 创建 `memory/index.md` — 按 type 分组（SOP/Principle/Duty）罗列全部概念，每条目含链接 + description 摘要
- [x] 4.2 创建 `memory/log.md` — 记录本次 OKF 迁移（日期 + Creation/Update 条目）

## 5. 合规验证

- [x] 5.1 运行 OKF v0.1 合规检查脚本遍历 `memory/`，确认所有非保留 `.md` 有 frontmatter + 非空 type → PASS
- [x] 5.2 确认 agent 代码无变更（`git diff --stat` 不含 agentmain.py/agent_loop.py/skill_loader.py/ga.py）
