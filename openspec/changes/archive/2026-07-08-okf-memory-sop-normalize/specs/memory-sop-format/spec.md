## ADDED Requirements

### Requirement: SOP 文档 SHALL 有 OKF 合规 frontmatter

`memory/` 目录下每个非保留 `.md` 文件（不含 `index.md`、`log.md`）MUST 在文件顶部包含可解析的 YAML frontmatter 块，且 frontmatter 中 MUST 包含非空 `type` 字段。这满足 OKF v0.1 合规要求。

frontmatter SHOULD 同时包含以下推荐字段：`title`（人类可读名称）、`description`（一句话摘要）、`tags`（领域关键词列表）、`timestamp`（ISO 8601 最后修改时间）。当 SOP 描述具体物理资产时，SHOULD 设 `resource` 字段指向该资产路径。

#### Scenario: 合规 SOP 文档

- **WHEN** 检查 `memory/tmwebdriver_sop.md`
- **THEN** 文件顶部有 `---` 界定的 YAML frontmatter，包含 `type: SOP`、`title`、`description`、`tags`、`resource: ../TMWebDriver.py`

#### Scenario: OKF 合规检查通过

- **WHEN** 运行 OKF v0.1 合规检查脚本遍历 `memory/` 目录
- **THEN** 每个非保留 `.md` 文件有可解析 frontmatter 且 `type` 非空，输出 PASS

### Requirement: type 字段 SHALL 使用 3 类分类法

`type` 字段 MUST 取以下三个值之一：`SOP`（操作流程）、`Principle`（原则红线）、`Duty`（角色职责）。consumer 容忍未知 type 值（按 generic concept 处理），但 producer MUST 使用这 3 类之一。

#### Scenario: 操作流程文档

- **WHEN** 文档描述的是一个任务或操作的执行流程（如 `plan_sop.md`、`tmwebdriver_sop.md`）
- **THEN** `type` 字段值为 `SOP`

#### Scenario: 原则红线文档

- **WHEN** 文档描述的是不可违反的规则或原则（如 `code_review_principles.md`）
- **THEN** `type` 字段值为 `Principle`

#### Scenario: 角色职责文档

- **WHEN** 文档描述的是某个角色的职责定义（如 `goal_hive_master_duty.md`）
- **THEN** `type` 字段值为 `Duty`

### Requirement: 文档间 SHALL 有稀疏交叉链接

当一个 SOP 的正确执行依赖另一个 SOP 时，MUST 在文档正文中用标准 markdown 链接指向被依赖的 SOP。链接 SHOULD 使用 OKF bundle-relative 绝对路径（以 `/` 开头，相对于 bundle 根）。不要求凡提及就链接——仅强依赖关系才加链接。

consumer MUST 容忍 broken link（目标不存在的链接不视为错误，代表尚未写就的知识）。

#### Scenario: 强依赖链接

- **WHEN** `goal_hive_sop.md` 的正确执行依赖 `goal_hive_master_duty.md` 中定义的 master 职责
- **THEN** `goal_hive_sop.md` 正文中包含指向 `goal_hive_master_duty.md` 的 markdown 链接

#### Scenario: 无过度链接

- **WHEN** 一个 SOP 仅在描述中提及另一 SOP 名称但无执行依赖
- **THEN** 不添加交叉链接，避免噪音

### Requirement: memory/ SHALL 包含 index.md 目录索引

`memory/index.md` MUST 存在，作为 OKF 目录索引文件，支持 progressive disclosure。`index.md` 不含 frontmatter（除非 bundle 根声明 `okf_version`）。正文按 section 分组罗列目录内容，每条目包含链接和 description。

#### Scenario: 浏览 memory/ 目录

- **WHEN** 人类或 agent 打开 `memory/index.md`
- **THEN** 看到按 type 分组的 SOP 列表，每条目有文件链接和一行 description 摘要

### Requirement: memory/ SHALL 包含 log.md 更新历史

`memory/log.md` MUST 存在，记录目录级更新历史。格式为日期分组条目列表（newest first），日期标题使用 ISO 8601 `YYYY-MM-DD` 格式。条目前导粗体词（`**Update**`/`**Creation**`/`**Deprecation**`）是约定非要求。

#### Scenario: 追踪 OKF 迁移

- **WHEN** 查看 `memory/log.md`
- **THEN** 看到本次 OKF 迁移的记录条目，标注日期和变更内容

### Requirement: 子目录 .md SHALL 同步合规

`memory/` 下的子目录（`autonomous_operation_sop/`、`review_sop/`）中的 `.md` 文件 MUST 同样合规 OKF v0.1（有 frontmatter + 非空 `type`）。子目录可有自己的 `index.md`。

#### Scenario: 子目录合规

- **WHEN** 运行 OKF 合规检查遍历 `memory/autonomous_operation_sop/`
- **THEN** 该目录下的 `.md` 文件有 frontmatter 且 `type` 非空

### Requirement: agent 读取逻辑 SHALL 不变

本次变更 MUST NOT 修改 agent 的 memory 加载逻辑（`agentmain.py`、`agent_loop.py`、`skill_loader.py`、`ga.py`）。L1 `global_mem_insight.txt` 继续作为运行时 prompt 注入索引，与新增的 `index.md`（离线浏览）并存。

#### Scenario: agent 运行时发现机制不变

- **WHEN** agent 启动并加载 memory
- **THEN** 仍通过 L1 → L2 → L3 按需 `file_read` 机制发现和加载 SOP，代码无变更
