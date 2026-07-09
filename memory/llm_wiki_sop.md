---
type: SOP
title: LLM Wiki 工业级知识库 SOP
description: 基于Karpathy模式+OKF规范+工程经验提炼的持久化知识库通用模式。任何LLM Agent+git repo即可实施。
tags: [llm-wiki, knowledge-base, okf, rag-alternative]
timestamp: 2026-07-09T00:00:00Z
---

## 0. 核心原理

**RAG vs LLM Wiki 的根本区别**：
- RAG：每次查询从原始文档重新检索+合成，零积累，重复劳动
- LLM Wiki：LLM增量构建并维护持久wiki，知识编译一次、持续更新，交叉引用/矛盾标记/综合分析随源增加而复合增长

**人机分工**：人负责策源、提问、方向判断；LLM负责摘要、交叉引用、归档、一致性维护——人类放弃wiki的原因是维护负担增长快于价值，LLM使维护成本趋近于零。

**设计哲学**：
- 本SOP只描述**模式和原则**，不绑定任何特定软件
- 最小可用实现 = 一个LLM Agent + 一个git repo + purpose.md + schema.md
- 工程经验来自Karpathy抽象模式、OKF互操作规范、以及工业级实现中验证过的可靠性模式

---

## 1. 项目初始化

### 1.1 目录结构（OKF合规）

```
my-wiki/
├── purpose.md              # 目标、关键问题、研究范围（人写，LLM读）
├── schema.md               # 页面类型定义、命名约定（人+LLM协作）
├── raw/
│   ├── sources/            # 原始文档（不可变，git追踪）
│   └── assets/             # 本地图片/媒体
├── wiki/
│   ├── index.md            # 内容目录（渐进披露，LLM自动维护）
│   ├── log.md              # 操作日志（ISO 8601日期，最新在前）
│   ├── overview.md         # 全局摘要（LLM自动更新）
│   ├── entities/           # 人物、组织、产品
│   ├── concepts/           # 理论、方法、技术
│   ├── sources/            # 源文档摘要
│   ├── queries/            # 保存的问答 + 研究结果
│   ├── synthesis/          # 跨源综合分析
│   └── comparisons/        # 对比分析
└── .wiki-state/            # 运行状态：摄入进度、review队列、图谱缓存
```

### 1.2 purpose.md 必须包含

```markdown
# 项目目的
## 核心问题
- [列出此wiki要回答的关键问题]
## 研究范围
- [领域边界，什么不属于此wiki]
## 产出目标
- [期望的输出格式和用途]
```

### 1.3 schema.md 必须包含

```markdown
# Wiki结构规则
## 页面类型
- entity: 人/组织/产品，frontmatter type=entity
- concept: 理论/方法，frontmatter type=concept
- source: 源文档摘要，frontmatter type=source
- query: 保存的问答，frontmatter type=query
- synthesis: 跨源分析，frontmatter type=synthesis
## 命名约定
- 文件名: kebab-case，如 `reinforcement-learning.md`
- 概念ID: 路径去.md后缀，如 `concepts/reinforcement-learning`
```

### 1.4 OKF Frontmatter 规范（每个wiki页面必须）

```yaml
---
type: entity|concept|source|query|synthesis  # 必填
title: 人类可读标题                              # 必填
description: 一句话描述                          # 必填
tags: [tag1, tag2]                             # 推荐
timestamp: 2026-07-09T00:00:00Z                # 推荐（ISO 8601）
sources: [raw/sources/doc1.pdf]                # 推荐（溯源）
---
```

**OKF合规性检查**：每个非保留.md文件必须有可解析的YAML frontmatter且type非空。

---

## 2. 摄入流水线（Two-Step Chain-of-Thought）

> **工程经验**：将摄入拆为"理解"和"写入"两步是可靠性的关键——理解步骤可缓存、可复用、可调试，写入步骤可回滚、可审查。

### 2.1 核心流程

```
源文档 → [Step1: LLM分析] → 结构化理解 → [Step2: LLM生成/更新] → wiki页面
```

**Step 1 — 分析（只读，可缓存）**：
- LLM读取源文档，提取：关键实体、核心概念、与现有wiki的关系、潜在矛盾
- 输出结构化分析结果（不直接写wiki）
- **增量缓存**：已分析过的源不重复分析（基于文件内容hash）
- 缓存失效条件：源文件修改、schema变更、purpose.md变更

**Step 2 — 生成/更新（写入，可审查）**：
- 基于Step1结果，LLM执行：
  1. 创建/更新实体页面（wiki/entities/）
  2. 创建/更新概念页面（wiki/concepts/）
  3. 创建源摘要（wiki/sources/）
  4. **交叉引用更新**：在相关页面添加双向链接
  5. **矛盾检测**：新信息与旧声明冲突时标记 ⚠️ contradiction
  6. 更新 overview.md 全局摘要
  7. 更新 index.md 目录
  8. 追加 log.md 操作日志

### 2.2 图片处理模式

- 从PDF提取嵌入图片 → 存入 raw/assets/
- Vision LLM生成事实性描述（非幻觉性标注）
- wiki页面用 ![描述](../raw/assets/img.png) 引用

### 2.3 可靠性模式（工业级关键）

> **工程经验**：摄入是最容易出错的环节。以下模式经工业级实现验证，确保崩溃安全、进度不丢失、结果可审计。

- **串行队列**：一次只摄入一个源，避免并发写入冲突
- **持久化进度**：记录当前处理到哪个源，崩溃/中断后从断点恢复
- **幂等性**：同一源重复摄入结果相同（Step1缓存 + Step2先读后写）
- **可取消/重试**：每个摄入任务可独立取消或重试
- **源变更监控**：检测 raw/sources/ 变更自动触发增量摄入（仅处理变更部分）

---

## 3. 查询流水线（Multi-Phase Retrieval）

> **工程经验**：单阶段检索召回率低（~58%），多阶段管道可提升到~71%+。关键是预算控制——无预算的检索会撑爆上下文窗口。

### 3.1 四阶段管道

```
Phase 1: 关键词搜索
  ├─ 分词（英文停用词去除 / 中文CJK分词）
  ├─ 标题匹配加分
  └─ 同时搜索 wiki/ 和 raw/sources/

Phase 2: 语义搜索（可选，默认关闭）
  ├─ 任意embedding模型 + 向量存储
  ├─ ANN余弦相似度检索
  └─ 合并到Phase1结果：提升已有匹配分 + 添加新发现

Phase 3: 图谱扩展
  ├─ 搜索结果作为种子节点
  ├─ 按相关度信号遍历知识图谱
  └─ 2跳遍历带衰减（越远权重越低）

Phase 4: 预算控制 + 上下文组装
  ├─ 可配置上下文窗口上限
  ├─ 比例分配: 60% wiki页面, 20% 对话历史, 5% index, 15% 系统
  ├─ 按综合分排序，预算内截断
  ├─ 编号页面含完整内容（非仅摘要）
  └─ 系统提示含: purpose.md, 语言规则, 引用格式, index.md
```

### 3.2 语义搜索启用条件

- wiki页面 > 100 时考虑启用（小wiki关键词足够）
- 独立配置embedding端点
- 基准：启用后召回率从~58%提升到~71%

### 3.3 查询结果引用

- LLM按编号引用: [1], [2] 等
- 引用指向具体wiki页面，可追溯

---

## 4. 知识图谱管理

> **工程经验**：纯文本wiki缺乏结构化关联发现。图谱的价值不在存储，而在**发现意外连接和知识缺口**——这是人类手动维护做不到的。

### 4.1 相关度信号模型

图谱边权重由多信号复合计算：

| 信号 | 说明 | 权重 |
|------|------|------|
| 直接链接 | wiki页面间的markdown链接 | 高 |
| 源重叠 | 两个页面引用相同源文档 | 中 |
| 共同邻居 | Adamic-Adar：共同邻居的流行度倒数之和 | 中 |
| 类型亲和 | 同类型页面间天然相关 | 低 |

### 4.2 社区检测

- 自动发现知识集群（如Louvain算法），计算凝聚度分数
- 用途：知识结构可视化、缺口发现、检索扩展

### 4.3 图谱洞察（Graph Insights）

- **意外连接**：跨社区边、跨类型链接，按惊喜分排序
- **知识缺口**：
  - 孤立页面（度≤1）：与wiki其余部分连接稀少
  - 稀疏社区（凝聚度低且≥3页）：内部交叉引用弱
  - 桥接节点（连接3+集群）：关键枢纽页面

### 4.4 交叉链接规范（OKF）

- **绝对链接（推荐）**：[文本](/concepts/topic.md) — bundle相对路径，移动稳定
- **相对链接**：[文本](./other.md) — 标准markdown相对路径
- **链接语义**：无类型化边，关系类型由上下文传达
- **容错**：消费者必须容忍断裂链接（可能代表尚未编写的知识）

---

## 5. 质量保障（工业级核心）

### 5.1 异步Review模式

> **工程经验**：同步review阻塞摄入流水线，导致知识库更新延迟。异步review让摄入持续进行，人类在方便时处理。

- LLM在摄入时标记需要人类判断的项（如矛盾裁决、低置信度提取）
- 预定义动作集：Create Page / Deep Research / Skip（约束防幻觉）
- 搜索查询在摄入时预生成（LLM优化关键词）
- 用户在方便时处理review，不阻塞摄入流程
- Review项持久化到 .wiki-state/reviews/

### 5.2 矛盾检测协议

当新信息与旧声明冲突时：
1. 在旧页面标记 ⚠️ Contradiction: [新源] 声明 X，但本页面记录 Y
2. 在新页面标记 ⚠️ Contradicts: [旧页面](/path/to/old.md)
3. 添加到Review队列供人类裁决
4. 人类裁决后：保留正确版本，另一版本标注为已否决+原因

### 5.3 OKF合规性自检

```bash
# 每个非保留.md必须有frontmatter且type非空
for f in $(find wiki/ -name "*.md" ! -name "index.md" ! -name "log.md"); do
  head -1 "$f" | grep -q "^---" || echo "FAIL: no frontmatter in $f"
done
```

### 5.4 源可溯性

- 每个wiki页面的frontmatter sources字段指向 raw/sources/ 中的原始文档
- 每个声明底部 # Citations 编号列出出处
- 引用格式：[1] [来源标题](URL或bundle相对路径)

---

## 6. 接口与集成原则

> **核心认知**：LLM Wiki的本质是"git repo of OKF-compliant markdown"。任何能读写文件的工具都是天然接口。

### 6.1 天然接口（零依赖）

| 接口 | 用途 | 示例 |
|------|------|------|
| 文件读写 | 读取/更新wiki页面 | 任意编辑器或Agent直接操作 |
| 全文搜索 | 关键词检索 | grep / ripgrep / 任何搜索工具 |
| git | 版本控制/历史/协作/回滚 | git log -- wiki/concepts/topic.md |
| LLM Agent | 摄入/查询/维护/扩展 | Agent直接读写文件，无需额外API |

**最小可用实现**：一个LLM Agent + 一个git repo + purpose.md + schema.md = 完整的LLM Wiki。

### 6.2 扩展接口模式（可选）

> 以下模式描述**如何设计**扩展接口，而非绑定特定实现。

**HTTP API 模式**（当需要远程访问/多Agent协作时）：
- 暴露核心能力：搜索、文件读取、图谱遍历、源重扫
- 默认只读，写操作需显式授权
- 安全原则：仅本地监听、Token认证、路径遍历防护、速率限制、请求体大小限制

**Agent工具协议模式**（当需要嵌入其他Agent运行时）：
- 将wiki能力注册为Agent可调用的工具
- 工具集：混合搜索、文件读取、图谱遍历、源重扫
- 默认只读，仅重扫为写操作

**安全设计原则**（任何接口实现必须遵循）：
1. 最小暴露：仅开放必要端点
2. 本地优先：默认仅监听localhost
3. 认证必须：Token/Key认证，常量时间比较防时序攻击
4. 路径安全：规范路径前缀检查，防遍历
5. 限流防护：并发限制 + 速率限制 + 请求体大小限制

---

## 7. 运维操作

### 7.1 Git版本控制

- 整个wiki是git repo → 免费获得版本历史/分支/协作
- 提交粒度：每次摄入一批源后提交一次
- 提交消息格式：ingest: added N sources, updated M wiki pages
- .gitignore：排除 .wiki-state/chats/（聊天历史）

### 7.2 备份策略

- raw/sources/：不可变，git追踪，**最关键**——源丢了wiki无法重建
- wiki/：LLM可重建但成本高，git追踪
- .wiki-state/：摄入进度+review项重要，聊天历史可丢弃

### 7.3 崩溃恢复

> **工程经验**：摄入是长时间操作，崩溃不可避免。关键原则：**所有可重建的数据不持久化，只持久化不可重建的进度状态**。

- 摄入进度持久化 → 崩溃后从断点恢复
- 向量索引可从wiki页面重建 → 不需要备份
- 知识图谱可从wiki交叉链接重建 → 不需要备份
- Review队列持久化 → 人类判断不丢失

### 7.4 Deep Research 模式

- 触发条件：知识缺口发现、用户显式请求
- 流程：LLM生成优化搜索主题 → 多查询web搜索 → 全内容提取 → 自动摄入 → 综合研究页面
- 用户确认：可编辑主题和搜索查询后再执行

---

## 8. 避坑清单（工业级关键）

| # | 坑 | 解法 |
|---|-----|------|
| 1 | **RAG惯性**：每次查询重新检索原始文档 | 坚持wiki优先查询，raw/sources/仅用于溯源验证 |
| 2 | **frontmatter缺失**：wiki页面无YAML头 | 摄入时强制检查，OKF合规自检 |
| 3 | **断裂链接**：页面移动后引用失效 | 使用绝对链接(/path/to.md)而非相对链接 |
| 4 | **摄入并发**：多源同时写入导致冲突 | 串行队列处理，一次只摄入一个源 |
| 5 | **矛盾未标记**：新旧信息冲突但未显式标注 | 摄入Step2必须执行矛盾检测+Review入队 |
| 6 | **overview过时**：全局摘要不反映最新状态 | 每次摄入后自动更新overview.md |
| 7 | **源不可溯**：wiki声明找不到出处 | frontmatter sources字段 + Citations节必须 |
| 8 | **过度语义搜索**：小wiki(<100页)启用向量搜索浪费 | 默认关闭，页面>100时考虑启用 |
| 9 | **Review堆积**：人类review项无限积压 | 定期(每周)清理review队列，批量处理 |
| 10 | **purpose.md缺失**：无明确目标wiki发散 | 初始化时必须写purpose.md，LLM摄入时读取作为约束 |
| 11 | **OKF过度合规**：消费者拒绝未知type/缺失可选字段 | 遵循OKF容错原则：MUST NOT reject for unknown types |
| 12 | **token预算失控**：大wiki查询超出上下文窗口 | Phase4 Budget Control必须启用，按比例分配 |
| 13 | **图谱孤岛**：大量孤立页面知识未连接 | 定期运行Graph Insights，Deep Research补桥接 |
| 14 | **schema未锁定**：页面类型随意增加导致混乱 | schema.md由人类审批，LLM不可自行添加新type |
| 15 | **接口过度暴露**：wiki API写权限过大 | 默认只读，写操作需显式授权+速率限制 |

---

## 9. 快速启动检查清单

- [ ] 创建项目目录，按1.1建立结构
- [ ] 撰写 purpose.md（目标+范围+产出）
- [ ] 撰写 schema.md（页面类型+命名约定）
- [ ] 初始化git repo
- [ ] 放入首批源文档到 raw/sources/
- [ ] 执行首次摄入（Step1分析 → Step2生成）
- [ ] 验证wiki页面生成（frontmatter/交叉引用/Citations）
- [ ] 运行OKF合规性自检
- [ ] git首次提交
- [ ] （可选）启用语义搜索（wiki>100页时）
- [ ] （可选）按需配置扩展接口（HTTP API / Agent工具协议）
- [ ] 设置定期review清理节奏
