## Context

当前配置路径：用户复制 `mykey_template.jsonc` → 手动取消注释 → 填入 apikey/model/apibase。有 `ga configure`（调 `configure_mykey.py`）但输出格式是 `mykey.py`。`mykey.jsonc` 是主推格式，缺少对应的交互式生成工具。

## Goals / Non-Goals

**Goals:**
- `ga setup` 子命令，交互式问答生成 `mykey.jsonc`
- 快速模式：最小 3 个问题即可生成可用配置
- 高级模式：快速完成后可继续配置更多参数
- 检测已有文件，提示覆盖/合并/退出

**Non-Goals:**
- 不修改 `configure_mykey.py`
- 不支持生成 `mykey.py`
- 不做 GUI
- 不支持 Mixin 配置（高级模式也跳过，太复杂）

## Decisions

### D1: 独立模块 `setup_wizard.py`

**选择**：新增 `ga_cli/setup_wizard.py`，不修改 `configure_mykey.py`。

**理由**：`configure_mykey.py` 是 1000+ 行的旧格式向导，输出 `.py`。新建独立模块避免引入回归风险，且职责清晰——一个输出 `.py`，一个输出 `.jsonc`。

### D2: 快速模式覆盖的字段

**选择**：apikey + 厂商选择（Claude/OpenAI/自定义）→ 自动推导 apibase 和 model。

**理由**：这是最小可用的三元组。用户只需要知道"我用哪个服务"和"key 是什么"，其他自动填。

### D3: 厂商列表

**选择**：内置 3 个快速选项：
1. Anthropic 官方（apibase: `https://api.anthropic.com`，model: `claude-opus-4-7`）
2. OpenAI 官方（apibase: `https://api.openai.com/v1`，model: `gpt-5.5`）
3. 自定义（手动输入 apibase + model）

**理由**：覆盖 90% 场景，不引入 `configure_mykey.py` 的复杂厂商枚举。

### D4: 已有文件处理

**选择**：检测 `mykey.jsonc` 存在时，提示 3 个选项：覆盖/退出/追加新配置。

**理由**：JSONC 是多配置块的，追加比覆盖更友好。但不做智能合并——JSONC 解析太复杂。

### D5: 输出格式

**选择**：用 Python 字符串模板生成 JSONC，保留注释标注每个配置块。

**理由**：JSONC 的核心价值是可读性和注释。模板生成比 JSON dump + 后加注释更可控。

## Risks / Trade-offs

- **覆盖/追加的复杂性**：JSONC 无标准解析器，追加模式只能简单地在文件末尾拼接。如果用户手动编辑过文件结构，追加可能破坏格式。→ 追加时先检查文件末尾是否可拼接，不可则回退到"另存为"模式
- **厂商配置准确性**：apibase 和 model 名字可能过时。→ 内置列表保持最小（3 个），不做在线校验
