# Reflect — 自动化看门狗脚本

> `reflect/` 目录包含通过 `agentmain.py --reflect` 运行的自动化脚本。
> 本质是**"外部闹钟"**：脚本自己决定什么时候叫醒 Agent。

---

## 运行机制

```
agentmain.py --reflect reflect/xxx.py
```

启动后进入 `--reflect` 模式（agentmain.py:264-300）：

1. 加载脚本模块，调 `init(extra_args)`（可选）
2. 每隔 `INTERVAL` 秒调一次 `check()`
3. `check()` 返回字符串 → 该字符串作为 prompt 发给 Agent 执行
4. `check()` 返回 `None` → 跳过，继续等待
5. `check()` 返回 `'/exit'` → 退出
6. Agent 执行完后调 `on_done(result)`（可选回调）
7. 支持脚本**热重载**（文件修改时自动重新加载）

### 标准接口

```python
INTERVAL = 60        # 轮询间隔（秒）
ONCE = False          # True = 只执行一次

def init(extra_args: dict):
    """可选：初始化，接收命令行额外参数"""
    pass

def check() -> str | None:
    """核心：返回 prompt 字符串触发 Agent，返回 None 跳过"""
    return "执行某个任务..."

def on_done(result: str):
    """可选：Agent 执行完后的回调"""
    pass
```

### 启动方式

- 命令行：`python agentmain.py --reflect reflect/goal_mode.py`
- 通过 hub.pyw：GUI 一键启停
- 通过 slash 命令：`/autorun`、`/goal` 等（frontends/slash_cmds.py）

---

## 5 个脚本详解

### 1. goal_mode.py — 死磕模式（INTERVAL=5s）

**场景**：给 Agent 一个目标 + 预算时间，走人。Agent 反复打磨直到预算耗尽。

**配置**：`temp/goal_state.json`
```json
{
  "objective": "写一篇万字深度报告",
  "budget_seconds": 7200,
  "max_turns": 50,
  "status": "running",
  "turns_used": 0
}
```

**循环三阶段**：
1. **创造**（第 1 次唤醒）：分析 objective，建工作文件夹，产出初版
2. **检验**：换视角审查（读者/测试工程师/领导），挑毛病，追求超预期
3. **改进**：根据检验报告实质性改进，禁止"无改动"

**收口**：预算耗尽 → Agent 总结进展、列未完成项、清理临时文件 → 结束。

**核心原则**：不允许宣告完成，持续唤醒直到预算用完。检验标准不够高时，升级检验标准而非放过。

#### 设计分析

**状态机**：

```
running → (预算耗尽 或 轮次超限) → wrapping_up → (agent 跑完收口轮) → done_budget
```

`check()` 每次读 `goal_state.json`，算 elapsed/remaining。状态非 `running` 或 JSON 不存在时返回 `'/exit'` 终止。

**两个 prompt 模板**：

| 模板 | 触发 | 内容 |
|---|---|---|
| `CONTINUATION_PROMPT` | 预算未耗尽 | 注入 objective + 已用/剩余时间 + 轮次，指导 创造→检验→改进 循环。检验不达标则"升级检验标准"而非放过 |
| `BUDGET_LIMIT_PROMPT` | 预算耗尽 | 收口：总结进展、列未完成项、清理临时文件。`on_done()` 回调标记 `done_budget` |

**框架耦合度**：90% 内核与框架无关（状态管理、时间计算、prompt 模板——纯 Python，零依赖），仅 10% 耦合在 GA `--reflect` 协议壳上：

| 接口 | 耦合原因 | 通用化方式 |
|---|---|---|
| `INTERVAL = 5` | GA 调度参数 | 改成目标框架的轮询间隔 |
| `ONCE = False` | GA 循环控制 | 改成目标框架的单次/持续开关 |
| `init(a)` | GA 配置注入 | 改成目标框架的初始化钩子 |
| `check() → str丨'/exit'` | GA prompt 注入协议 | 改成目标框架的任务产生接口 |
| `on_done(result)` | GA 结果回调 | 改成目标框架的后处理钩子 |

**结论**：Goal Mode 本质是框架无关的**带时间预算的自治循环状态机**（objective + budget + status JSON → 定时注入 prompt），换框架只需替换外层 5 个接口适配点，内核逻辑不用动。

---

### 2. autonomous.py — 无人值守（INTERVAL=30min）

**场景**：用户离开电脑，Agent 自主找事做。

**机制**：每 30 分钟发固定 prompt：
```
[AUTO]🤖 用户已经离开超过30分钟，作为自主智能体，请阅读自动化sop，执行自动任务。
```

Agent 自行决定干什么（整理记忆、跑维护、清理临时文件等），参考 `memory/autonomous_operation_sop.md`。

---

### 3. agent_team_worker.py — BBS 接单工人（INTERVAL=60s）

**场景**：多个 GA 实例组成团队，通过 HTTP BBS 协调分工。

**配置**：`reflect/agent_team_setting.json`
```json
{"base_url": "http://bbs.example.com", "board_key": "xxx", "name": "worker-1"}
```

**机制**：每 60 秒轮询 BBS `/posts` 接口：
- 有新帖 → 返回 prompt 让 Agent 抢单、执行、发帖汇报
- 无新帖 → `None`，继续等
- 连续失败 10 次 → `/exit`

**行为规范**：抢单要确认最早接单、不和别人重复、严格区分交付物和报告信息、不回应纯 ACK 帖。

---

### 4. checklist_master.py — 清单包工头（INTERVAL=60s）

**场景**：复杂任务拆成多个子任务，需要 Master 派活。

**配置**：`state.json`（由 init 的 `mr_folder` 参数指定路径）

**两种模式**：
- **checklist 模式**：Master 自己执行未完成的子任务
- **mapreduce 模式**（配了 BBS）：Master **只派发+验收，绝不自己执行**

**触发条件**：
- 有新 BBS 回帖 → 去验收
- 有未完成任务 → 继续派发/执行
- 都完成了 → 推进 plan 下一步

---

### 5. scheduler.py — 定时任务调度器（INTERVAL=120s）

**场景**：按时间表自动触发 Agent（如每天 3 点备份、每周一生成周报）。

**配置**：`sche_tasks/` 目录下放 `.json` 任务文件：
```json
{
  "enabled": true,
  "repeat": "daily",
  "schedule": "03:00",
  "max_delay_hours": 6,
  "prompt": "执行数据库备份..."
}
```

**重复模式**：`once` / `daily` / `weekday` / `weekly` / `monthly` / `every_Nh` / `every_Nm` / `every_Nd`

**机制**：每 120 秒扫描所有任务，检查：已到计划时间 + 冷却期已过 + 未超过最大延迟窗口 → 触发。执行结果写入 `sche_tasks/done/<timestamp>_<id>.md`。

**附加功能**：每 12 小时静默触发 L4 会话压缩（调 `memory/L4_raw_sessions/compress_session.py`），归档 `temp/model_responses/` 里的原始会话日志。

**端口锁**：bind 127.0.0.1:45762 防止重复启动。

---

## Goal Hive — 多 worker 协作协议

Goal Hive 不是新脚本，是 **Goal Mode + agent_team_worker + BBS 的组合协议**。复用同一个 `reflect/goal_mode.py` 引擎，通过配置差异实现多 agent 协作。

### 架构：三层角色

```
┌──────────────┐
│   BBS Server │  assets/agent_bbs.py — FastAPI + SQLite，纯 HTTP
└──────┬───────┘
       │
┌──────┴───────┐
│  Hive Master │  reflect/goal_mode.py（同引擎，定制 goal_state.json）
│  只调度不干活  │  objective 里嵌入 BBS 地址 + 职责描述 + 调度指令
└──────┬───────┘
       │ 拆任务、派发到 BBS
┌──────┴───────┐
│   Workers    │  reflect/agent_team_worker.py × N（≤5个）
│  只干活不决策  │  轮询 BBS → 抢单 → 执行 → 发帖汇报
└──────────────┘
```

### Master 的控制论模型

Goal Hive Master 用控制论建模工作流，不同于单机 Goal Mode 的 创造→检验→改进：

- **J\*** = 用户真正要的价值（不变）
- **y** = 当前产物
- **e** = J\* − y（偏差）
- 每轮目标 = 测 e、压 e

四阶段循环，发散/收敛交替：

| 阶段 | 模式 | 谁做 | 产出 |
|---|---|---|---|
| x.1 探测 | 发散（多 worker 并行调研） | workers | `探测报告Tx.md` |
| x.2 设计 | 收敛（Master 独断） | Master 亲自 | `执行方案Tx.md`（含 changelog） |
| x.3 执行 | 发散（多 worker 并行实施） | workers | 产物增量合入锚点 |
| x.4 检查 | 发散（多视角独立挑刺） | workers | `检查报告Tx.md`（P0/P1 清单 → 下轮 changelog） |

**失稳急刹**：worker 忙但 J 不升、局部多整体不可用、过程取代用户价值 → 立即停派、重对齐 J\*、砍弱任务、恢复闭环。

### 与单机 Goal Mode 的对比

| 维度 | Goal Mode | Goal Hive |
|---|---|---|
| agent 数 | 1 | 1 Master + N workers（≤5） |
| 引擎 | `reflect/goal_mode.py` | **同一个** `reflect/goal_mode.py` |
| 差异来源 | - | `goal_state.json` 的 objective 嵌入了 BBS 地址 + 调度职责 |
| 工作方式 | 同一 agent 创造→检验→改进 | Master 拆/汇，workers 并行执行 |
| 收口 | 总结进展 + 清理 | 同上 + 关闭所有 worker + BBS 宣告结束 |

### 框架耦合度：Hive 比单机更通用

单机 Goal Mode 的 agent 和引擎是**直接耦合**的——`check()` 返回值直接注入 GA agent 循环。Hive 在中间插了一层 BBS：

```
单机：goal_mode.py → GA agent（直接耦合）
Hive：goal_mode.py → GA agent → BBS ← Worker（BBS 解耦）
```

| 组件 | 与 GA 耦合 | 通用性 |
|---|---|---|
| BBS Server | **零**。FastAPI + SQLite，纯 HTTP | 100% |
| Hive Master | 仅 5 行 `--reflect` 协议壳 | 90%（同 goal_mode.py） |
| Worker | 仅 5 行 `--reflect` 协议壳 | 90%（同 agent_team_worker.py） |

**BBS 是关键解耦点**：workers 不需要是 GA agent——任何能发 HTTP 请求的 agent 框架（LangChain、AutoGPT 等）都能当 worker。换框架只需替换 Master/Worker 的 5 行接口适配，BBS 一行不用动。

---

## 总结对比

| 脚本 | INTERVAL | 触发条件 | 核心功能 |
|---|---|---|---|
| `goal_mode` | 5s | Agent 跑完就触发 | 死磕一个目标到预算耗尽 |
| `autonomous` | 30min | 固定间隔 | 无人值守，Agent 自找事做 |
| `agent_team_worker` | 60s | BBS 有新帖 | 多 Agent 团队接单协作 |
| `checklist_master` | 60s | 有未完成任务/新回帖 | 项目经理派活+验收 |
| `scheduler` | 120s | 到计划时间 | 定时执行（daily/weekly 等） |
