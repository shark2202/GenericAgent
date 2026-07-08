## Context

GenericAgent 的 `memory/` 目录包含 20 个 `.md` 文件（SOP、原则、职责文档）和若干 `.py` 工具脚本，构成 L3 任务级记录库。当前状态：

- **零 frontmatter**：20 个 `.md` 文件全部以 `# Title` 开头，无 YAML 元数据
- **零交叉链接**：文件间无 markdown 链接，关联关系不可见
- **无目录索引**：靠 L1 `global_mem_insight.txt`（≤30 行手工索引）做发现，无 `index.md`
- **格式不统一**：每个文件结构随意，无 `type`/`description`/`tags` 等可程序化解析的字段

OKF v0.1（Open Knowledge Format）是一个极简格式规范：目录树 + markdown + YAML frontmatter（唯一硬性字段 `type`）+ 交叉链接 + `index.md`/`log.md`。无 SDK、无注册中心、`cat` 可读、`git diff` 友好。

## Goals / Non-Goals

**Goals:**
- 使 `memory/*.md` 合规 OKF v0.1（每个非保留 `.md` 有可解析 frontmatter + 非空 `type`）
- 通过交叉链接使文档间强依赖关系可见
- 通过 `index.md` 提供 progressive disclosure 目录浏览
- 通过 `type` 分类（SOP/Principle/Duty）支持程序化过滤

**Non-Goals:**
- 不改 agent 读取逻辑（L1→L2→L3 按需 `file_read` 机制不变）
- 不建知识图谱引擎、不引入图查询
- 不改 `memory/*.py` 工具脚本
- 不纳入 `docs/`（项目文档，scope 另定）
- 不加 pre-commit hook 或自动化校验（规模不够，手动跑 okf 合规检查即可）
- 不替换 L1（L1 = 运行时 prompt 注入；index.md = 离线浏览；两者并存）

## Decisions

### D1: type 分类法 — 3 类（SOP / Principle / Duty）

**选择**：`SOP`（15 个操作流程）/ `Principle`（code_review_principles）/ `Duty`（goal_hive_master_duty）。`computer_use.md` 和 `subagent.md` 归为 `SOP`（它们是"如何使用 X"的操作指南）。

**理由**：全标 `SOP` 则 `type` 字段无意义；4+ 类则维护负担过重。3 类是"刚好能区分性质"的最小粒度。OKF 规范规定 type 值不集中注册，producer 自选描述性值，consumer 容忍未知 type。

**备选**：全标 `SOP`（ rejected——丢失分类信息）；按文件名前缀自动分类（rejected——文件名不统一，且 OKF 鼓励语义 type 而非机械分类）。

### D2: 交叉链接策略 — 稀疏，仅强依赖

**选择**：仅当一个 SOP 的正确执行依赖另一个 SOP 时才加链接。不凡提及就链。

**理由**："最小充分指针"适用于链接。过度链接 = 噪音。OKF 规范容忍 broken link（代表尚未写就的知识），但这不意味着应该过度链接。

**示例**：`goal_hive_sop.md` → `goal_hive_master_duty.md`（执行 hive 流程需知 master 职责）；`tmwebdriver_sop.md` → `vision_sop.md`（浏览器自动化常配合视觉识别）。

### D3: L1 保留，index.md 并存

**选择**：不替换 L1 `global_mem_insight.txt`，新增 `memory/index.md` 作为 OKF 目录索引。

**理由**：L1 是 agent 运行时每轮注入 prompt 的 ≤30 行策展索引，always-in-context；index.md 是离线/人类浏览的完整目录列表。不同消费者、不同用途、不冗余。删 L1 会破坏 agent 运行时发现机制（改 agent 代码 = scope 爆炸）。

### D4: resource 字段 — 仅文件绑定型

**选择**：仅当 SOP 描述的是具体物理资产时设 `resource`。大部分 SOP 是抽象流程，省略。

**示例**：`tmwebdriver_sop.md` → `resource: ../TMWebDriver.py`；`plan_sop.md` 无 resource（描述规划流程，无绑定资产）。

### D5: tags — 自由填写，不预设受控词表

**选择**：用明显领域关键词（如 `[browser, automation]`、`[memory, planning]`），不预设词表。

**理由**：预设受控词表是 premature optimization。OKF consumer 容忍任意 tags。模式涌现后再收敛。

### D6: 子目录 .md 纳入

**选择**：`memory/autonomous_operation_sop/*.md` 和 `memory/review_sop/*.md` 同步合规。

**理由**：OKF bundle = 整棵目录树。排除子目录 = 不完整 bundle，合规检查（`os.walk`）会覆盖它们。

## Risks / Trade-offs

- **[Risk] frontmatter 增加 token 开销** → `file_read` 时多 ~5 行 YAML。影响极小（< 50 tokens/file），且 agent 按需读取单个 SOP 时不影响 L1 注入预算。
- **[Risk] 交叉链接过时** → SOP 内容变更后链接可能断裂。OKF 规范明确容忍 broken link（"代表尚未写就的知识"），不视为错误。手动维护即可。
- **[Risk] type 分类主观** → 边界文件（如 `computer_use.md` 是 SOP 还是 Reference？）判断有主观性。选择归 SOP 因为它是"如何使用 computer use 工具"的操作指南。可后续调整，OKF 允许 producer 自由定义 type。
- **[Trade-off] index.md 与 L1 内容部分重叠** → 两者都列出 SOP 名称。但 L1 是策展版（≤30 行，只列高频），index.md 是完整版（全部 + description）。重叠可接受，因消费者不同。
