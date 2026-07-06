# 记忆系统与自进化机制

> GA 的灵魂：LLM 驱动的循环在运行时按需读取知识(md)和执行工具(py)，任务结束后把经验结晶回写为新知识——形成自进化的知识-行动闭环。

---

## 一、4 层记忆架构

```
L0  memory_management_sop.md        ← 宪法（记忆管理的元规则）
    ↓ 规定怎么写记忆
L1  global_mem_insight.txt (≤30行)   ← 索引层（路由表）
    ↓ 导航指针
L2  global_mem.txt                   ← 事实库（环境配置/凭证/路径）
    ↓ 详细引用
L3  memory/*.md | *.py              ← 任务SOP + 可执行工具（39个文件）
    ↓
L4  L4_raw_sessions/                 ← 原始会话历史（自动归档）
```

**设计哲学**：上层只存指针，下层存细节。L1 极简（≤30 行 <1k tokens）保证每轮注入不爆 context。

### L0：宪法层（memory_management_sop.md）

4 条核心公理，约束 Agent 怎么写记忆：

| 公理 | 内容 |
|---|---|
| **行动验证原则** | 只能写入**经过工具验证成功**的信息，禁止写猜测/推理/固有知识。`No Execution, No Memory` |
| **神圣不可删改** | 已验证的数据只能压缩/迁移，不能丢弃准确性 |
| **禁止存易变状态** | 不存 PID、时间戳、临时 Session ID |
| **最小充分指针** | 上层只留能定位下层的最短标识 |

决策树（L0:73-92）：
- 环境特异事实（IP/路径/凭证）→ L2
- 通用避坑规律 → L1 RULES
- 特定任务技术 → L3
- 通用常识 → 丢弃，严禁存储

### L1：索引层（≤30 行）

结构（`assets/global_mem_insight_template.txt`）：
```
L0(META-SOP): memory_management_sop
L2: 现空
L3: memory_cleanup_sop | ui_detect.py | plan_sop | ...

浏览器特殊操作: tmwebdriver_sop(...)
键鼠: ljqCtrl_sop(...)

[RULES]
1. 搜索先行: 搜文件名严禁不用es...
2. 交叉验证: 禁信摘要...
```

**两层映射**：高频场景 key→value（直接给文件名），低频场景仅列关键词。

### L2：事实库

`global_mem.txt`，按 `## [SECTION]` 组织环境事实（路径、凭证、配置、常量）。随环境扩展而膨胀（可接受）。

### L3：任务SOP + 工具

`memory/*.md`（声明性知识）+ `memory/*.py`（命令性工具）。39 个文件，按需读取，不预加载。

### L4：原始会话历史

`memory/L4_raw_sessions/`，自动归档压缩的原始 LLM 响应日志。含 `compress_session.py`（压缩工具）和 `salient_mining_sop.md`（挖掘指南：情绪事件/持续活动/已消失事项）。

---

## 二、运行时注入机制

记忆不是预加载的，而是**按需注入**：

```
┌─ system_prompt 构建 (agentmain.py:153)
│    get_system_prompt() = sys_prompt.txt + get_global_memory()
│    get_global_memory() = L1全文 + 结构说明 + cwd路径  (ga.py:583)
│
├─ 每 10 轮重新注入 (ga.py:569)
│    turn_end_callback: turn % 10 == 0 → next_prompt += get_global_memory()
│
├─ 工作记忆每轮注入 (ga.py:544-548)
│    _get_anchor_prompt(): <history>最近30条</history> + <key_info>工作记忆</key_info>
│
└─ L3 按需读取
     Agent 遇到任务 → 读 L1 索引 → 发现相关 SOP → file_read("memory/xxx_sop.md")
```

**关键**：L1 每轮都注入（轻量），L3 只在需要时 `file_read` 读取（按需）。

### 工作记忆（短期，进程内）

```python
# ga.py:441 do_update_working_checkpoint
if "key_info" in args: self.working['key_info'] = key_info
if "related_sop" in args: self.working['related_sop'] = related_sop
self.working['passed_sessions'] = 0
```

- `key_info`：当前任务关键信息，每轮注入
- `related_sop`：关联 SOP 路径，提示不清晰时重读
- **跨任务继承**（agentmain.py:157-161）：新 handler 继承旧 handler 的 key_info + passed_sessions 计数

### 节奏注入（turn_end_callback, ga.py:551）

| 轮次 | 动作 |
|---|---|
| 每轮 | 提取 `<summary>` → 追加到 history_info |
| turn % 7 | 提醒保存 checkpoint，停止无效重试 |
| turn % 10 | 重新注入 global_memory（L1） |
| turn % 25 | 提醒写文件持久化关键发现 |
| turn % 175 | 强制 ask_user 汇报进度，防止空转 |

---

## 三、自进化机制

### 核心工具 `do_start_long_term_update` (ga.py:508)

**这个工具本身不写记忆**。它做两件事：
1. 把 L0 规则（memory_management_sop.md）作为 tool_result 返回给 Agent
2. 返回一个 prompt 告诉 Agent **怎么提取和写入**

然后 Agent 自己用 `file_read`（看现有）+ `file_patch`（最小化修改）来更新 L1/L2/L3。

**自进化 = Agent 调用工具获取规则 → Agent 遵循规则写文件 → 文件成为未来记忆**。

### 触发时机

| 方式 | 机制 |
|---|---|
| **Agent 自主** | `start_long_term_update` 在工具 schema 里，LLM 自己决定何时调用 |
| **System prompt 引导** | `update_working_checkpoint` 注释提示"任务成功完成应该是 start_long_term_update" |
| **非自动** | 不强制每任务触发，依赖 LLM 判断"有值得记的就调" |

### 写入约束（从 L0 继承）

- 只能 `file_patch`，禁止 overwrite
- 写前必读现有内容
- 无新内容则跳过
- 对记忆库最小局部修改

### L4 自动归档

`scheduler.py` 每 12 小时静默触发（scheduler.py:62-74）：

```python
from compress_session import batch_process
batch_process('temp/model_responses/', dry_run=False)
```

把 `temp/model_responses/` 原始 LLM 响应日志压缩归档到 `L4_raw_sessions/`，文件名 `MMDD_HHMM-MMDD_HHMM.txt`。

---

## 四、闭环图

```
任务执行
   │
   ├─ 每轮：L1 注入 + 工作记忆注入 → LLM 决策
   │         ↓
   │    Agent file_read L3 SOP → 按SOP执行
   │         ↓
   │    工具执行 → 验证结果 → 更新工作记忆
   │
   └─ 任务完成
        │
        ├─ Agent 调 start_long_term_update
        │     ↓
        ├─ 工具返回 L0 规则 + 提取prompt
        │     ↓
        ├─ Agent 按规则 file_patch 更新 L1/L2/L3
        │     ↓
        └─ 新记忆写入 → 下次任务可被 L1 索引路由到

   后台（每12h）：L4 归档原始会话 → 可供挖掘
```

---

## 五、为什么有效

### 1. 解决了 context window 的核心矛盾

LLM context 有限，但知识库可以无限大。L1 是"目录页"（≤30行），不是"百科全书"。Agent 知道**有什么能力**，需要时才 `file_read` 读**怎么用**。

### 2. "行动验证"阻断了幻觉污染

`No Execution, No Memory` — 只能写入工具执行验证过的信息。防止 Agent 把幻觉写进记忆污染未来，是记忆可信度的护城河。

### 3. 自进化产生复利效应

```
任务A 遇坑 → 记坑到 SOP → 任务B 类似坑 → 读SOP → 避坑 → 更快完成
→ 有余力做更难任务 → 新经验 → 新SOP → ...
```

每次任务的产出不只交付物，还有可复用的记忆。

### 4. 节奏注入对抗注意力衰减

LLM 在长对话中对早期内容注意力衰减。GA 对策：每 10 轮重注入 L1，每轮注入 key_info，每 7 轮提醒存 checkpoint，每 175 轮强制 ask_user。

### 5. patch-only 防止灾难性丢失

只能 `file_patch`（局部修改），不能 overwrite。Agent 一时糊涂也最多改坏一小段，不会把整个 SOP 覆盖掉。爆炸半径控制。

### 6. 跨任务工作记忆继承

新 handler 继承旧 handler 的 key_info + passed_sessions 计数。Agent 知道"我之前在做什么、发现了什么"。

---

## 六、度量现状（诚实评估）

**GA 没有正式的度量框架。** 没有 benchmark、没有 A/B test、没有 metrics dashboard。

### 现有的隐含反馈信号

| 信号 | 机制 | 局限 |
|---|---|---|
| 任务成功/失败 | 最直接的质量信号 | 没有系统级追踪 |
| 三次失败规则 | sys_prompt 里"3次失败请求干预" | 没有计数器统计触发频率 |
| 轮次作为效率代理 | max_turns=180，更少轮次=更高效 | 没有跨任务轮次追踪 |
| L4 会话挖掘 | scheduler 每12h 归档 + salient_mining | 产物给 Agent 读，不是给人看的 metrics |
| 记忆增长 | SOP 文件数/L2 条目增长 | 没有"被引用多少次"统计 |

### 可以度量但没有的指标

| 指标 | 怎么度量 |
|---|---|
| **SOP 命中率** | Agent 读 SOP 后任务成功率 vs 未读时 |
| **重复犯错率** | 同类错误在不同任务中出现的频率 |
| **轮次趋势** | 同类任务轮次随时间变化 |
| **记忆引用率** | 每个 SOP 被 file_read 的次数 |
| **记忆写入质量** | 新写入的 SOP 是否在后续任务中被用到 |
| **自进化触发率** | 多少任务触发了 start_long_term_update |

### 最小成本度量方案

在 `file_read` 和 `start_long_term_update` 里加访问日志：

```python
# ga.py file_read 加访问日志
def log_memory_access(path):
    with open('temp/memory_access.log', 'a') as f:
        f.write(f"{time.time()}\t{path}\n")

# do_start_long_term_update 加触发计数
with open('temp/evolution_events.log', 'a') as f:
    f.write(f"{time.time()}\tstart_long_term_update\n")
```

这样就能统计：哪些 SOP 被读得最多、自进化多久触发一次、哪些任务后写了记忆。

---

## 七、核心设计巧思

1. **L0 自己也在 memory 里**——记忆的规则本身就是记忆，元递归
2. **Agent 自己写记忆**，不是系统自动提取——LLM 判断什么值得记
3. **只 patch 不 overwrite**——防止 Agent 一时糊涂把好记忆覆盖掉
4. **L1 ≤30 行硬约束**——保证索引层永远轻量，不会随记忆增长而膨胀
5. **行动验证原则**——防止 Agent 把幻觉写进记忆污染未来
