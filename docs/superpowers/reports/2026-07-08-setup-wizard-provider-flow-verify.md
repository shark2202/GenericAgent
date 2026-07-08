# 验证报告：setup-wizard-provider-flow

**变更**: setup-wizard-provider-flow
**工作流**: tweak
**验证日期**: 2026-07-08
**验证结果**: PASS

## 验证范围

本报告验证 `ga setup` 向导的提供商优先流程重构，包括：
1. `fetch_models()` 函数实现（GET /v1/models 动态模型发现）
2. `run_setup_wizard()` 流程重排（供应商 → apibase → key → models → model）
3. `generate_config_block()` 签名更新（apibase/model 改为显式参数）
4. `_QUICK_PROVIDERS` 移除硬编码 model 名

## 验证步骤与结果

### 1. 导入检查

```
python3 -c "import ga_cli.setup_wizard; print('OK')"
```

**结果**: PASS — 模块无导入错误。

### 2. 编译检查

```
python3 -m py_compile ga_cli/setup_wizard.py
```

**结果**: PASS — 无语法错误。

### 3. 单元测试：generate_config_block 新签名

验证三个供应商的配置块生成：

- **Anthropic**: `generate_config_block('anthropic', key, apibase='https://api.anthropic.com', model='claude-sonnet-4-5-20250929', block_index=0)`
  - PASS: apibase 和 model 正确写入
  - PASS: `thinking_type: adaptive` extras 保留
  - PASS: 无硬编码 `claude-opus-4-7`

- **OpenAI**: `generate_config_block('openai', key, apibase='https://api.openai.com/v1', model='gpt-4o', block_index=0)`
  - PASS: apibase 和 model 正确写入
  - PASS: `api_mode: chat_completions` extras 保留
  - PASS: 无硬编码 `gpt-5.5`

- **Custom**: `generate_config_block('custom', key, apibase='https://my-proxy.com/v1', model='custom-model', block_index=0)`
  - PASS: apibase 和 model 正确写入

### 4. _QUICK_PROVIDERS 无硬编码 model

```python
assert 'model' not in _QUICK_PROVIDERS['anthropic']  # PASS
assert 'model' not in _QUICK_PROVIDERS['openai']      # PASS
```

### 5. fetch_models URL 构造

验证 URL 构造遵循 `auto_make_url` 约定：

- Anthropic: `https://api.anthropic.com` → `https://api.anthropic.com/v1/models` ✓
- OpenAI: `https://api.openai.com/v1` → `https://api.openai.com/v1/models` ✓

### 6. fetch_models 失败回退

使用无效 key 调用 `fetch_models('https://api.anthropic.com', 'invalid-key', 'anthropic')`：

**结果**: PASS — 返回 `None`，不抛异常。

### 7. 端到端流程模拟

使用 mock 模拟完整向导流程（Custom 供应商路径）：

- 输入：provider=3, apibase=`https://my-proxy.com/v1`, key=`sk-test...`, model=`my-custom-model`
- `fetch_models` 返回 `None`（网络失败）
- 回退到手动输入 model 名
- 配置块正确生成
- 向导完成无崩溃

**结果**: PASS

### 8. 认证头区分

代码审查确认：
- Anthropic: `x-api-key` + `anthropic-version: 2023-06-01` ✓
- OpenAI/Custom: `Authorization: Bearer <key>` ✓

## 未覆盖项

- 有效 API key 的真实 `/v1/models` 调用（需真实凭证，无法在 CI 中验证）
- OpenAI 响应格式运行时验证（代码兼容 `{data: [{id:...}]}` 格式，若不同则 fallback）

## 结论

所有可验证的需求均已通过。实现符合 delta spec 中 MODIFIED "Provider Auto-Configuration" 需求的全部场景。
