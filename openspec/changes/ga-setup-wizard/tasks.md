## 1. 核心模块

- [x] 1.1 创建 `ga_cli/setup_wizard.py`，实现 `run_setup_wizard()` 入口
  - 快速模式：厂商选择 → API key → 生成配置块
  - 高级模式：proxy、timeout、max_tokens、temperature、context_win

- [x] 1.2 实现 JSONC 生成逻辑
  - Anthropic → NativeClaudeSession 配置块
  - OpenAI → NativeOAISession 配置块
  - Custom → 用户指定 apibase + model 的通用配置块

- [x] 1.3 实现已有文件检测与处理
  - 检测 `mykey.jsonc` 是否存在
  - 覆盖 / 退出 / 追加

## 2. CLI 集成

- [x] 2.1 在 `ga_cli/cli.py` 注册 `setup` 子命令
  - 添加 `COMMANDS["setup"]` 条目
  - 指向 `setup_wizard.run_setup_wizard()`

## 3. 验证

- [x] 3.1 快速模式生成 Anthropic 配置 → 文件内容正确
- [x] 3.2 快速模式生成 OpenAI 配置 → 文件内容正确
- [x] 3.3 快速模式生成自定义配置 → 文件内容正确
- [x] 3.4 高级模式追加参数 → 配置块包含追加字段
- [x] 3.5 已有文件检测 → 覆盖/退出/追加 三条路径均正确
- [x] 3.6 `ga --help` 显示 `setup` 命令
