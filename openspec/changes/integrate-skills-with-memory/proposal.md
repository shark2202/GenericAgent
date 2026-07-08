## Why

当前 Skill 支持（`add-skill-support` change 产物）将 Skill catalog 作为 flat list 全量追加到 system_prompt 末尾，完全旁路了项目的 L1-L4 记忆 + 自进化架构。Skill 不在 L1 索引中注册、不参与自进化闭环、无使用经验回写、token 随 Skill 数量线性膨胀。这导致 Skill 成为一个割裂的平行知识体系，无法享受项目核心的"知识-行动闭环"复利效应。

## What Changes

- **BREAKING**: 移除 `get_skill_catalog()` 对 system_prompt 的全量注入（`agentmain.py:46`），改为通过 L1 索引层路由
- `skill_loader.py` 职责变更：从"格式化 catalog 字符串"改为"扫描 Skill → 同步到 L1 索引"（自动同步 + Agent 可微调）
- L1 索引（`global_mem_insight.txt`）新增 Skills 段：结构化多行，每个 Skill 一行含路径指针
- 新增 Skill 经验文件机制：Agent 用 Skill 后，经验写入 `memory/skill_exp_<name>.md`（L3），L1 单条目同时指向 SKILL.md 和经验文件
- 工作记忆 `related_sop` 复用：Skill 使用记录跨任务继承（`agentmain.py:159-163` 机制）
- 可选增强：opt-in 模式下，Agent 发现 SKILL.md 事实错误 → 备份原版 → patch 修正

## Capabilities

### New Capabilities
- `skill-memory-integration`: Skill 作为 L3 独立子类接入 L1-L4 记忆体系，含 L1 路由、自进化经验回写、工作记忆追踪

### Modified Capabilities
- `skill-discovery`: Skill 发现结果不再全量注入 system_prompt，改为同步到 L1 索引；热加载从"每轮 re-scan + inject"变为"同步到 L1"

## Impact

- `skill_loader.py`：核心重写，从 catalog 格式化改为 L1 同步
- `agentmain.py`：移除 `get_skill_catalog()` 调用（line 46）
- `ga.py`：工作记忆 `related_sop` 机制复用（可能需微调以识别 Skill 路径）
- `memory/global_mem_insight.txt`：新增 Skills 段格式
- `memory/skill_exp_*.md`：新增经验文件类别
- 不影响 `agent_loop.py`、`frontends/`、LLM session 管理
