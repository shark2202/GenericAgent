---
type: SOP
title: skill_sop
description: Skill 生命周期管理——安装、发现、使用、经验回写、更新、退役。
tags: [skill, lifecycle]
timestamp: 2026-07-08T00:00:00Z
---

# skill_sop

## 定义
Skill 是外部作者产物的可执行知识包，入口为 SKILL.md。与 L3 SOP 平级但信任边界不同：SOP 是 Agent 自进化写入，Skill 是外部作者产物。

## 目录约定
- User-level: `~/.agents/skills/<name>/SKILL.md`
- Project-level: `.agents/skills/<name>/SKILL.md`（同名覆盖 user-level）
- frontmatter 必须有 `name` 字段，否则静默跳过

## 生命周期

### 1. 安装
Copy 目录到 skills root（含 SKILL.md + frontmatter）。无需注册——下次 session 自动发现。

### 2. 注册（自动）
`sync_skills_to_l1()` 每次 `get_global_memory()` 时扫描 skills 目录 → 写入 L1 `<!-- auto-skills-start -->` ~ `<!-- auto-skills-end -->` 之间。

### 3. 发现
L1 [Skills] 段格式：
- **Hot**（有 `memory/skill_exp_<name>.md`）：`<name>: <SKILL.md路径> | <经验文件路径>` → 直接 read_file
- **Cold**（无经验文件）：逗号分隔的名字列表 → 按目录约定拼路径：`~/.agents/skills/<name>/SKILL.md`

### 4. 使用
`read_file` SKILL.md → 按其指令执行。SKILL.md 内含完整工作流、步骤、脚本路径。

### 5. 经验回写
Skill 使用后，通过 `do_start_long_term_update` → `file_patch` 写入 `memory/skill_exp_<name>.md`。下次 sync 自动将该 skill 从 cold 升级为 hot（L1 条目追加经验文件路径）。

### 6. 更新
- **外部更新**（git pull / 手动替换）：直接覆盖 SKILL.md，下次 sync 路径不变
- **Agent 修改**：需 `GA_SKILL_PATCH_ENABLED=1` + 先调 `backup_and_patch_skill()` 备份，再 `file_patch` 修改

### 7. 退役
删目录 → 下次 sync 自动从 L1 移除条目。`skill_exp_<name>.md` 需手动清理（或保留备查）。

## 典型坑
- Cold skill 在 L1 只有名字无路径 → 按约定 `~/.agents/skills/<name>/SKILL.md` 自己拼
- frontmatter 无 `name` 字段 → 静默跳过，不报错
- sync 只改 marker 之间内容，marker 外手动备注保留
- Agent 改 SKILL.md 必须先 backup（`GA_SKILL_PATCH_ENABLED=1`）
- `skill_exp_<name>.md` 命名：`skill_exp_` 前缀 + skill 的 frontmatter name（kebab-case）
