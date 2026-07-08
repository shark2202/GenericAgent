---
comet_change: ga-setup-wizard
role: technical-design
canonical_spec: openspec
---

# Design Doc: ga setup 交互式配置向导

## 架构概览

```
ga setup (ga_cli/cli.py)
  └─→ setup_wizard.run_setup_wizard()
        ├─ detect_existing()        → 覆盖/退出/追加
        ├─ quick_setup()            → apikey + provider → mykey.jsonc
        └─ advanced_setup()         → proxy, timeout, max_tokens, etc.
```

## 模块设计

### setup_wizard.py

两个核心函数 + 一个入口：

- `run_setup_wizard()` — 主入口，编排流程
- `generate_config_block(provider, apikey, **advanced)` — 生成 JSONC 配置块字符串
- `detect_existing(path)` — 检测已有文件，返回用户选择

### 交互流程

```
1. 检测 mykey.jsonc → 存在则 [覆盖/退出/追加]
2. API Key? (隐藏输入, 最小校验: 非空 + ≥20字符)
3. 厂商? [1] Anthropic [2] OpenAI [3] 自定义
   - 1/2 → 自动填 apibase + model + 厂商特定默认值
   - 3   → 追问 apibase + model
4. 生成配置块 → 写入 mykey.jsonc
5. 高级选项? [y/N]
   - y → proxy, connect_timeout, read_timeout, max_tokens, temperature, context_win
   - N → 退出
```

### 配置块模板

**Anthropic**:
```jsonc
"native_claude_config{N}": {
  "name": "claude",
  "apikey": "{apikey}",
  "apibase": "https://api.anthropic.com",
  "model": "claude-opus-4-7",
  "thinking_type": "adaptive"
}
```

**OpenAI**:
```jsonc
"native_oai_config{N}": {
  "name": "openai",
  "apikey": "{apikey}",
  "apibase": "https://api.openai.com/v1",
  "model": "gpt-5.5",
  "api_mode": "chat_completions"
}
```

## 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| API key 校验 | 非空 + ≥20 字符 | 中转站 key 格式各异，不强校验前缀 |
| 配置块命名 | 自动编号 `config{N}` | 支持追加，避免 key 冲突 |
| 追加检测 | 检查 `{` 开头 `}` 结尾 | 简单启发式，异常时建议覆盖 |
| 厂商默认值 | thinking_type/adaptive, api_mode/chat_completions | 90% 场景的最优默认值 |
| 终端兼容 | 复用 ga_cli 的 GBK 处理 | 不重复造轮子 |

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 追加到损坏 JSONC | 检查 { } 结构，异常建议覆盖 |
| 厂商 model 名过时 | 最小列表（3 个），文档引导手动改 |
| 隐藏输入跨平台 | 用 `getpass.getpass()`，Windows/macOS/Linux 通用 |
