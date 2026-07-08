# Brainstorm Summary

- Change: ga-setup-wizard
- Date: 2026-07-07

## 确认的技术方案

新增 `ga_cli/setup_wizard.py`，在 `ga_cli/cli.py` 注册 `setup` 子命令。交互流程：检测已有文件 → 快速模式（3 问题：apikey + 厂商 + 自动推导 apibase/model）→ 可选高级模式（proxy/timeout/max_tokens/temperature/context_win）。配置块自动编号防冲突。厂商选 Anthropic 自动设 thinking_type:adaptive，选 OpenAI 自动设 api_mode:chat_completions。

## 关键取舍与风险

- 最小校验（非空 + ≥20 字符），不做前缀/格式校验（中转站 key 格式各异）
- 追加模式检测 JSONC 结构（{ 开头 } 结尾），异常时建议覆盖
- 内置 3 个厂商，不做在线校验（model 名可能过时）
- 复用 ga_cli 已有的 Windows GBK 兼容代码

## 测试策略

- 单元：generate_config_block() + detect_existing()
- 集成：完整 ga setup 流程，检查生成文件
- 边界：空输入、Ctrl+C、权限错误、损坏 JSONC

## Spec Patch

无
