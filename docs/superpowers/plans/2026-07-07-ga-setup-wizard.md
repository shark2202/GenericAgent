---
change: ga-setup-wizard
design-doc: docs/superpowers/specs/2026-07-07-ga-setup-wizard-design.md
base-ref: aa0b707d9fb8d60c88ece7a1719e50b9fb693ada
archived-with: 2026-07-08-ga-setup-wizard
---

# Plan: ga setup 交互式配置向导

## 实施概览

新增 `ga_cli/setup_wizard.py`，在 `ga_cli/cli.py` 注册 `setup` 子命令。

## 任务

### 1. 核心模块

- [ ] 1.1 创建 `ga_cli/setup_wizard.py`，实现 `run_setup_wizard()` 入口
- [ ] 1.2 实现 JSONC 生成逻辑（Anthropic/OpenAI/Custom）
- [ ] 1.3 实现已有文件检测与处理（覆盖/退出/追加）

### 2. CLI 集成

- [ ] 2.1 在 `ga_cli/cli.py` 注册 `setup` 子命令

### 3. 验证

- [ ] 3.1-3.6 六项端到端验证

## 执行顺序

1 → 2 → 3

