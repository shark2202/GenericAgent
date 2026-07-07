## Why

GenericAgent 的配置入口是手动编辑 `mykey.jsonc`——一个 100+ 行、包含 12 种配置类型、需要理解 `mixin`/`native`/`apibase` 等概念的 JSONC 模板文件。现有 `ga configure` 输出的是旧格式 `mykey.py`。新用户从零到可用的路径太长。增加 `ga setup` 交互式向导，3 个问题即可生成最小可用配置。

## What Changes

- 新增 `ga_cli/setup_wizard.py`：交互式问答，分快速/高级两步，生成 `mykey.jsonc`
- 修改 `ga_cli/cli.py`：新增 `setup` 子命令入口
- 快速模式：API key → 选择厂商 → 选择 model → 生成文件
- 高级模式：快速完成后可选配置 proxy、timeout、context_win 等高级参数

## Capabilities

### New Capabilities

- `interactive-setup`: 交互式 `ga setup` CLI 向导，通过问答生成最小可用的 `mykey.jsonc`，分快速和高级两步

### Modified Capabilities

<!-- No existing specs to modify -->

## Impact

- 涉及文件：`ga_cli/cli.py`（+1 子命令）、新增 `ga_cli/setup_wizard.py`
- 不影响：`assets/configure_mykey.py`（保留兼容）、`llmcore.py`、`agentmain.py`
- 依赖：无新外部依赖
