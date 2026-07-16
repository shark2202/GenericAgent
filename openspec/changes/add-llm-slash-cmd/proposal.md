## Why

GA 的 agent loop 支持多 LLM provider 配置（`list_llms()`/`next_llm()`），但用户在对话中无法查看当前可用模型列表或运行时切换模型。TUI v1/v2 已有 `/llm` 命令，但核心 agent loop 的 `_handle_slash_cmd()` 未暴露此入口，导致 CLI 模式下用户无法交互式切换模型。

## What Changes

- 在 `_handle_slash_cmd()` 中新增 `/llm` 子命令处理
- `/llm` 无参数：列出所有可用模型及当前激活模型
- `/llm <index>`：切换到指定索引的模型
- 复用现有 `list_llms()` 和 `next_llm()` 函数，零侵入

## Capabilities

### New Capabilities

- `llm-slash-cmd`：对话中通过 `/llm` 查看/切换 LLM 模型

### Modified Capabilities

_(无现有 spec 需修改)_

## Impact

- **代码**：仅修改 `agentmain.py` 的 `_handle_slash_cmd()` 方法，新增约 15 行
- **依赖**：无新依赖，复用现有 `list_llms()`/`next_llm()`
- **API**：无 API 变更
- **兼容性**：完全向后兼容，新增命令不影响现有行为
