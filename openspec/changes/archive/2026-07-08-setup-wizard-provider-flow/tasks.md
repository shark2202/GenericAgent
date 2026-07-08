# 任务

## 1. 实现 `fetch_models()` 函数

- [x] 1.1 在 `ga_cli/setup_wizard.py` 中新增 `fetch_models(apibase, apikey, provider)` 函数
  - 使用 `requests.get`，10 秒超时
  - Anthropic：请求头 `x-api-key` + `anthropic-version: 2023-06-01`
  - OpenAI/Custom：请求头 `Authorization: Bearer <key>`
  - GET `<apibase>/v1/models`（规范化尾部斜杠）
  - 成功返回 `(display_name, id)` 元组列表
  - 任何失败（超时、HTTP 错误、网络错误、JSON 解析错误）返回 `None`

- [x] 1.2 处理两个厂商的响应解析
  - Anthropic：`response.json()["data"]` → 每项有 `id`、`display_name`（display_name 缺失时用 `id` 兜底）
  - OpenAI：`response.json()["data"]` → 每项只有 `id`（用 `id` 作为 display_name）
  - 列表过长时截断为前 20 项

## 2. 重构 `run_setup_wizard()` 流程

- [x] 2.1 重排流程：选供应商 → apibase（有默认值，可覆盖）→ API key → fetch_models → 选 model
- [x] 2.2 供应商选择：保留现有 1/2/3 菜单（Anthropic/OpenAI/Custom）
- [x] 2.3 apibase 提示：Anthropic/OpenAI 显示默认值，Custom 为空；回车接受默认
- [x] 2.4 API key 提示：与当前一致（掩码输入）
- [x] 2.5 模型选择：调用 `fetch_models()`，成功展示编号列表，失败回退手动输入
- [x] 2.6 保留现有：detect_existing()、_ask_advanced()、generate_config_block()、文件写入逻辑

## 3. 更新 `_QUICK_PROVIDERS`，移除硬编码 model

- [x] 3.1 移除供应商默认值中的硬编码 model 名（`claude-opus-4-7`、`gpt-5.5`）
- [x] 3.2 保留 `_QUICK_PROVIDERS` 中的默认 apibase 值用于展示

## 4. 验证

- [x] 4.1 运行 `python -m ga_cli setup`，确认新流程端到端可用
- [x] 4.2 无效 key 测试 → 回退手动输入 model 正常
- [x] 4.3 有效 key 测试 → model 列表展示和选择正常
- [x] 4.4 apibase 覆盖测试 → 自定义 apibase 用于 fetch_models 并写入配置
- [x] 4.5 Custom 供应商测试 → 手动输入 apibase、model 回退正常
- [x] 4.6 已有文件检测和高级模式仍正常
- [x] 4.7 运行 `python -c "import ga_cli.setup_wizard"` 确认无导入错误
