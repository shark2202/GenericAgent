# 核心架构：agent_loop.py + agentmain.py

> 两个文件构成 GA 的**引擎层 + SDK 层**。

---

## agent_loop.py — 纯引擎（框架无关）

无 GA 业务知识（无 memory/skill/frontend），只管 LLM↔工具 循环。

### 核心函数 `agent_runner_loop()` (line 42)

是一个 **generator**，调用方增量消费输出：

```
while turn < max_turns:
    1. LLM 生成 → yield 文本流
    2. 解析 tool_calls
    3. 逐个 dispatch 工具 → handler.do_<tool_name>(args) → 返回 StepOutcome
    4. 根据 StepOutcome 决定下一步
```

### StepOutcome (line 6) — 控制流原语

| 字段 | 含义 |
|---|---|
| `data` | 工具返回值 → 变成 tool_results 发回 LLM |
| `next_prompt` | 下一轮 user message。**None = 任务完成** |
| `should_exit=True` | **跳出循环**（ask_user 用此暂停等用户） |

### 关键设计

- **line 104**：`messages = [{"role": "user", "content": next_prompt, ...}]`，每轮**只发新消息**，完整对话历史存在 `client.backend`（LLM session）里，不在这里累积。
- **退出条件**：`should_exit` → EXITED；`next_prompt=None` → CURRENT_TASK_DONE；到 max_turns → MAX_TURNS_EXCEEDED；`_done_hooks` 队列未空则继续。
- **BaseHandler** (line 16) 是抽象基类，`dispatch()` 调 `do_<tool_name>`。GenericAgentHandler(ga.py) 继承它实现具体工具。
- **Hooks**：在 tool_before/after, turn_before/after, llm_before/after, agent_before/after 八个点调用 `_hook()`，无插件时为 no-op。

### verbose vs non-verbose

- verbose=True：yield 原始 markdown chunk（含 `````` 代码块标记）
- verbose=False：用 `_clean_content` 压缩代码块，`_compact_tool_args` 紧凑显示工具参数

---

## agentmain.py — SDK 层 + 多模式入口

### GenericAgent 类 (line 51) — 线程安全 SDK

**生产者-消费者模式**：
- `put_task(query)` → 返回 `display_queue` (line 117)
- `run()` 在 daemon 线程里循环取任务 (line 138)
- 所有 16 个 frontend 都用这个 API

### run() 执行流 (line 138-193)

1. 取任务 → 处理 slash 命令 (`/session.k=v`, `/resume`)
2. 长 prompt (>2000) 落盘替引用
3. 构建 system_prompt = base + global_memory + extra_prompts
4. 创建新 GenericAgentHandler，**继承上个 handler 的 key_info**（工作记忆跨任务传递，line 157-161）
5. 调 `agent_runner_loop(yield_info=True)` 并消费 generator：
   - `{'turn': N}` → 更新轮次
   - 文本 chunk → 累积，每 30 字符推一次 display_queue
   - 检查 `_stop` 文件 / `stop_sig` → abort
6. 完成 → 推 `{'done': full_resp}`，更新 `self.history`

### display_queue 协议

- `{'next': text, 'turn': N, 'outputs': [...]}` — 增量/全量输出
- `{'done': full_resp, ...}` — 任务完成
- `{'done': ..., 'source': 'system'}` — slash 命令结果

### LLM session 管理 (line 68-109)

从 mykey.py 自动发现配置 → NativeClaudeSession / NativeOAISession / MixinSession(故障转移)。`next_llm()` 切换时**转移对话历史**。

### Slash 命令

- `/session.key=value` → set attribute on llmclient.backend (runtime config override)
- `/resume` → 生成 prompt 让 Agent 列出最近可恢复的会话

---

## 4 种运行模式 (`__main__`, line 197-323)

| 模式 | 触发 | 行为 |
|---|---|---|
| **CLI 交互** (默认) | 无参数 | daemon 线程跑 run()，主线程 `input()` 循环，Ctrl+C → abort |
| **--task IODIR** | `--task NAME` | 先 fork 自身到后台（stdout→log），读 input.txt→写 output.txt，多轮等 reply.txt |
| **--func FILE** | `--func prompt.md` | 纯函数：读 prompt → 写 prompt.out.txt → 退出，单次 |
| **--reflect SCRIPT** | `--reflect script.py` | 加载监控脚本，轮询 `check()`，触发时发任务，支持热重载 + on_done 回调 |

---

## 两文件交互关系

```
agentmain.GenericAgent.run()
  │
  ├─ put_task() → display_queue   (线程安全队列)
  │
  ├─ agent_runner_loop()  ← generator，yield 文本流
  │     │
  │     ├─ llmclient.chat()       (历史存在 backend 里)
  │     │
  │     └─ handler.dispatch()     (GenericAgentHandler from ga.py)
  │           └─ do_<tool>() → StepOutcome
  │
  └─ 消费 generator → 推 display_queue → frontend 读取
```

---

## 5 个关键设计决策

1. **历史在 LLM session 里**，不在 messages 数组 — 避免每轮传巨量消息
2. **Generator 流式** — agent_runner_loop 是 generator，run() 桥接到 queue 实现线程安全
3. **StepOutcome 解耦** — 工具不直接控制循环，只返回 outcome，循环统一解释
4. **BaseHandler 可复用** — agent_loop.py 无 GA 业务，换个 handler 就能驱动别的工具集
5. **工作记忆跨任务继承** — 新 handler 继承旧 handler 的 key_info + passed_sessions 计数
