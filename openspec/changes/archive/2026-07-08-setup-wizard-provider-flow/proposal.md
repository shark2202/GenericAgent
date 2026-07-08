## Why

`ga setup` 向导当前流程有两个问题：(1) 先问 API key 再选供应商，但供应商决定认证头格式和 models 端点，顺序反了；(2) model 名硬编码（`claude-opus-4-7`、`gpt-5.5`），容易过时，且用户看不到自己的 key 能用哪些 model。此外 Anthropic 的 apibase 被写死为 `https://api.anthropic.com`，用户无法走代理或第三方兼容端点。

两个厂商都支持 `GET /v1/models` 端点（已验证：Anthropic 和 OpenAI 均返回 401 非 404），可用于动态拉取可用 model 列表并顺带验证 key 有效性。

## What Changes

- **重构向导流程**：选供应商 → API Base URL（有默认值可覆盖）→ API key → 动态拉取 model 列表 → 选 model
- **apibase 可覆盖**：Anthropic 和 OpenAI 均显示默认 apibase，回车接受，可手动覆盖（不再写死）
- **动态 model 发现**：用用户填的 apibase + key 调 `GET /v1/models`，成功则展示列表供选择，失败则回退手动输入
- **key 验证**：models 请求成功即验证了 key + apibase 都正确
- **保留**：已有文件检测、高级模式、Custom 供应商路径不变

## Capabilities

### New Capabilities
<!-- 无新 capability -->

### Modified Capabilities
- `interactive-setup`: 修改供应商配置流程——从"硬编码 apibase + model"改为"apibase 可覆盖 + 动态 model 发现 + key 验证"

## Impact

- **修改文件**：`ga_cli/setup_wizard.py`（重构 `run_setup_wizard()` 流程 + 新增 `fetch_models()` 函数）
- **不修改**：`ga_cli/cli.py`（setup 子命令注册不变）、`llmcore.py`、`agentmain.py`
- **无新依赖**：使用已有的 `requests` 库（llmcore.py 已依赖）
- **网络调用**：新增一个 `GET /v1/models` 请求（有超时和 fallback，不阻塞流程）
