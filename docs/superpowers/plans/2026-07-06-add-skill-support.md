---
change: add-skill-support
design-doc: docs/superpowers/specs/2026-07-06-add-skill-support-design.md
base-ref: 046e5f6bc16f8dd0d955cff38782c7f47874bcf5
---

# Plan: AGENT SKILL 加载支持

## 实施概览

新增 `skill_loader.py` 模块，在 `agentmain.py` 的 `get_system_prompt()` 中注入 skill catalog。
改动量极小：1 个新文件 + 1 行修改。

## 任务

### 1. 核心模块

- [ ] 1.1 创建 `skill_loader.py`，实现 `get_skill_catalog() -> str`
- [ ] 1.2 编写 catalog 格式化输出

### 2. 集成到 agentmain.py

- [ ] 2.1 在 `get_system_prompt()` 末尾追加 skill catalog
- [ ] 2.2 验证 `file_read` 工具可直接加载 SKILL.md 路径

### 3. 验证

- [ ] 3.1 启动 GA，检查 system_prompt 是否包含 skill catalog
- [ ] 3.2 测试热加载
- [ ] 3.3 测试优先级覆盖
- [ ] 3.4 测试边界情况

## 执行顺序

1 → 2 → 3（依赖顺次）
