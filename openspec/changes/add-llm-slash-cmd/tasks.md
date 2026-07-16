## Tasks

### 1. Build

- [x] 1.1 在 `_handle_slash_cmd()` 中新增 `elif cmd == "llm":` 分支，实现查看/切换模型逻辑
- [x] 1.2 确认 `list_llms` 和 `next_llm` 已在模块顶部 import

### 2. Verify

- [x] 2.1 验证 `/llm` 无参数输出所有模型列表及当前标记
- [x] 2.2 验证 `/llm 1` 切换到索引 1 的模型并显示确认消息
- [x] 2.3 验证 `/llm 99` 超出范围时显示错误提示
- [x] 2.4 验证 `/llm abc` 非数字参数时显示用法提示
- [x] 2.5 `ruff check` 无新违规
