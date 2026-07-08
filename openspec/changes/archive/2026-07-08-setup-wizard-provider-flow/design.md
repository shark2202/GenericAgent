## Context

`ga_cli/setup_wizard.py` 当前 220 行，流程为：检测已有文件 → API key → 选供应商(1/2/3) → 生成配置。model 名硬编码在 `_QUICK_PROVIDERS` 字典中。两个厂商的 `GET /v1/models` 端点已验证存在（Anthropic HTTP 401 需 x-api-key，OpenAI HTTP 401 需 Bearer）。

## Goals / Non-Goals

**Goals:**
- 重构流程为：供应商 → apibase(可覆盖) → key → 动态 model 列表 → 选 model
- apibase 不再写死，所有供应商均可覆盖
- 动态拉取 model 列表，失败有 fallback

**Non-Goals:**
- 不改 cli.py 注册逻辑
- 不改 configure_mykey.py
- 不支持 Mixin 配置
- 不做 model 列表缓存

## Decisions

### D1: 流程顺序改为供应商优先

**选择**：选供应商 → apibase → key → models → 选 model

**理由**：供应商决定认证头格式（x-api-key vs Bearer）和端点路径，必须先知道供应商才能用 key 拉 models。

### D2: apibase 有默认值但可覆盖

**选择**：显示默认 apibase，回车接受，可手动输入覆盖。

**理由**：用户可能走代理或第三方兼容端点。Anthropic 不能写死 `https://api.anthropic.com`。

### D3: model 发现用 GET /v1/models，失败回退手动输入

**选择**：用用户实际填的 apibase + key 调 `GET /v1/models`。成功展示列表供选；失败（超时/401/404/网络错误）回退手动输入 model 名。

**理由**：端点存在性已验证但不保证所有第三方兼容端点都实现。fallback 保证流程不中断。

### D4: 认证头按供应商区分

**选择**：
- Anthropic：`x-api-key: <key>` + `anthropic-version: 2023-06-01`
- OpenAI 兼容：`Authorization: Bearer <key>`

**理由**：已通过 401 错误消息验证两个厂商的认证机制。Anthropic SDK 源码确认 response 为 `ModelInfo`（含 `id`、`display_name`）。

### D5: model 列表展示 display_name + id

**选择**：列表展示 `display_name (id)` 格式，用户选编号，写入 config 用 `id`。

**理由**：Anthropic SDK `ModelInfo` 有 `display_name`（人类可读）和 `id`（API 调用用）。OpenAI 只有 `id`，展示时用 `id` 作为 display_name 的 fallback。

## Risks / Trade-offs

- **第三方端点不实现 /v1/models** → fallback 到手动输入，不阻塞
- **model 列表很长** → 分页或截断显示前 20 个，提示用户也可手动输入
- **key 无效时 models 请求失败** → fallback 手动输入，用户配完后运行时才会发现 key 问题（可接受，与当前行为一致）
- **OpenAI response 格式未运行时验证** → 代码兼容 `{data: [{id:...}]}` 格式，若格式不同则 fallback
