# AGENTS.md

本文件记录 GenericAgent 仓库的验证/构建命令，供 opencode 及其他 agent 协作时参考。
完成代码改动后，请运行下列命令验证（当前环境：Windows / pwsh）。

## Python 主项目

- **安装（开发模式）**：`uv pip install -e ".[ui]"`
- **Lint**：`ruff check .`
- **测试**：`pytest tests/`
- **覆盖率（可选）**：`pytest tests/ --cov`（需先 `pip install pytest-cov`）

> `pyproject.toml` 已声明 `[tool.ruff]` / `[tool.pytest.ini_options]` / `[tool.coverage.run]`。
> 现有代码可能存在历史违规，`ruff check .` 当前会报已知违规；新代码应保证不引入新违规。
> 待 dev CI 落地前需先做一轮 ruff 修复，使全仓 lint 转绿。

## Go 子项目（`go-agents/`）

在 `go-agents/` 目录下执行：

- **构建**：`go build ./...`
- **测试**：`go test ./...`

## 桌面前端（`frontends/desktop/`，Tauri）

在 `frontends/desktop/src-tauri/` 目录下执行：

- **构建检查**：`cargo check`

## Release / 分发

- **便携分发包**：推 `v*` tag 触发 `.github/workflows/release.yml`（4 架构交叉构建，产出含自包含 CPython 运行时的 portable archive）。
- **本地组装**：`scripts/assemble-dist-local.sh`。
- **Python 运行时打包**：`scripts/bundle-python.sh <arch>`（arch ∈ win-x64 / mac-arm64 / mac-x64 / linux-x64）。

## 暂未启用

- **类型检查**：项目未配置 mypy / pyright。如需引入，先在 `pyproject.toml` 加 `[tool.mypy]` 并在此登记命令。
- **CI（push/PR 触发）**：当前仅有 release CI（tag 触发）；dev/test/staging 三环境 CI 尚未落地，详见评估方案文档。

## 环境约定

- **Python 版本**：3.11 或 3.12（勿用 3.14，与 `pywebview` 及部分依赖不兼容）。
- **支持平台**：Windows / macOS / Linux。
- **包管理器**：`uv`。
- **主分支**：`main`；集成分支：`dev`。打 `v*` tag 前应先在 `dev` 上完成 dev/test 验证。
