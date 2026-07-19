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

## Shell 脚本（`scripts/`）

- **Lint**：`shellcheck scripts/*.sh scripts/installers/*.sh`（需先装 shellcheck；Windows 可 `choco install shellcheck` 或用 WSL/git-bash 自带）。新脚本应保证不引入新违规。

## Release / 分发

- **便携分发包**：推 `v*` tag 触发 `.github/workflows/release.yml`（4 架构交叉构建，产出含自包含 CPython 运行时的 portable archive）。
  > **计划废弃**：本地安装器方案落地后将停用此 CI，改用下面的 `release-local.sh`（见 `docs/plans/2026-07-09-local-installer-packaging-design.md`）。
- **本地组装**：`scripts/assemble-dist-local.sh`（version 默认从 `pyproject.toml` 读，off-tag 加 `+g<sha>[.dirty]`）。
- **Python 运行时打包**：`scripts/bundle-python.sh <arch>`（arch ∈ win-x64 / mac-arm64 / mac-x64 / linux-x64）。
- **本地一键发布**：`scripts/release-local.sh <arch> [--dry-run]`（bundle → assemble → build-installer；`--dry-run` 只到压缩包）。
- **安装器构建**：`scripts/build-installer.sh <arch>`（Win=Inno `.exe`、mac=`.pkg`、Linux=`.deb`+`.rpm`）。
- **安装器验收**：见 `scripts/installers/TESTING.md`（每版本人工 checklist）。

## 暂未启用

- **类型检查**：项目未配置 mypy / pyright。如需引入，先在 `pyproject.toml` 加 `[tool.mypy]` 并在此登记命令。
- **CI（push/PR 触发）**：当前仅有 release CI（tag 触发）；dev/test/staging 三环境 CI 尚未落地，详见评估方案文档。

## 环境约定

- **Python 版本**：3.11 或 3.12（勿用 3.14，与 `pywebview` 及部分依赖不兼容）。
- **支持平台**：Windows / macOS / Linux。
- **包管理器**：`uv`。
- **主分支**：`main`；集成分支：`dev`。打 `v*` tag 前应先在 `dev` 上完成 dev/test 验证。

## 特性 env gates（hermes skill-scoring 闸门）

| env | 默认 | 作用 |
|---|---|---|
| `GA_SKILL_SCORER` | (缺省:`_subagent_mgr` present → `subagent`;absent → `inprocess`) | skill 打分闸门实现选择。`subagent`=真隔离子 agent;`inprocess`=同进程二次 LLM(弱独立兜底);`off`/`none`/`false`/`0`=v1 行为(无打分直接落盘) |
| `GA_SKILL_SCORER_THRESHOLD` | `60` | 打分闸门阈值,`verdict==pass` 且 `score >= 阈值` 才放行 |
| `GA_SKILL_SCORER_TIMEOUT` | `600` | `SubagentScorer` 子 agent 超时(秒),超时 → 降级 `InProcessScorer` |
