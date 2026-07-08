## Why

GenericAgent 的 `memory/*.md`（20 个 SOP/原则/职责文档）目前是无结构的裸 markdown：无 frontmatter 元数据、零交叉链接、无目录索引。这导致两个问题：(1) 文档格式不统一，无法被程序化解析和过滤；(2) 文档间关联关系不可见，发现相关 SOP 只能靠 L1 手工索引或 `ls` 盲猜。

采纳 OKF v0.1（Open Knowledge Format）格式规范——一个极简的、无依赖的、`cat` 可读的 markdown + YAML frontmatter 标准——可以在近零成本下实现知识表示的规范化与可发现性，且与 GenericAgent 的"最小充分指针""能简单就别复杂"哲学完全对齐。

## What Changes

- **新增** YAML frontmatter 到 `memory/*.md` 全部 20 个文档（`type` / `title` / `description` / `tags` / `timestamp`），使其合规 OKF v0.1
- **新增** 文档间交叉链接（稀疏，仅强依赖关系），使关联可见
- **新增** `memory/index.md`（OKF 目录索引，progressive disclosure），罗列全部概念及其 one-line description
- **新增** `memory/log.md`（OKF 更新历史，可选）
- `type` 字段采用 3 类分类：`SOP`（操作流程）/ `Principle`（原则红线）/ `Duty`（角色职责）
- `resource` 字段仅用于文件绑定型 SOP（如 tmwebdriver_sop → `../TMWebDriver.py`）
- **不改动** agent 读取逻辑（L1/L2/L3 加载机制不变）、**不改动** `.py` 工具脚本、**不引入** 任何新依赖

## Capabilities

### New Capabilities
- `memory-sop-format`: memory/ 下知识文档的 OKF v0.1 格式规范——frontmatter 元数据结构、交叉链接规则、index.md 目录索引、log.md 更新历史、type 分类法（SOP/Principle/Duty）

### Modified Capabilities
<!-- 无现有 spec 级别的行为变更。skill-discovery spec 不受影响（agent 读取逻辑不变）。 -->

## Impact

- **修改文件**：`memory/*.md` 全部 20 个文件（追加 frontmatter + 少量交叉链接）
- **修改文件**：`memory/autonomous_operation_sop/*.md`、`memory/review_sop/*.md`（子目录内 .md 同步合规）
- **新增文件**：`memory/index.md`、`memory/log.md`
- **不修改**：`agentmain.py`、`agent_loop.py`、`skill_loader.py`、`ga.py` 等 agent 代码
- **不修改**：`memory/*.py` 工具脚本（OKF 明确排除源代码）
- **不修改**：`docs/`（项目文档，scope 另定）
- **无新依赖**：OKF 是格式规范不是工具，零安装
- **L1 保留**：`global_mem_insight.txt` 继续作为 agent 运行时 prompt 注入索引，与 `index.md`（离线浏览）并存
