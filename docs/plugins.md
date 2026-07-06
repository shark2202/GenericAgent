# Plugins — 钩子扩展系统

> `plugins/` 是 GA 的插件层，通过钩子（hooks）机制在不修改核心代码的前提下扩展 Agent 行为。
> 与 `reflect/`（外部轮询式自动化）互补：`plugins/` 是**内部注入式扩展**（Agent 干活时额外干什么）。

---

## hooks.py — 钩子引擎（67 行）

### 核心 API

```python
# 注册回调
@hooks.register('turn_before')
def my_hook(ctx: dict) -> dict:
    # ctx 是 locals() 快照，可读取/修改
    ctx['messages'].append(...)  # 直接 mutate 生效
    return ctx  # 返回 dict 则替换 ctx

# 触发（由 agent_loop.py 调用）
hooks.trigger('turn_before', locals())

# 其他
hooks.unregister('turn_before', my_hook)
hooks.clear('turn_before')  # 或 clear() 清空所有
hooks.has('turn_before')    # → bool
```

### 自动发现

`discover_and_load()` (line 46)：启动时扫描 `plugins/` 目录，自动 import 所有非 `_` 开头的 `.py` 文件。在 `agentmain.py:12` 调用：

```python
try:
    from plugins.hooks import discover_and_load; discover_and_load()
except Exception: pass
```

---

## 8 个钩子点

在 `agent_loop.py` 的 `agent_runner_loop()` 中灌注：

| 钩子 | 触发时机 | ctx 关键内容 |
|---|---|---|
| `agent_before` | 任务开始 | client, system_prompt, user_input, handler, tools_schema |
| `agent_after` | 任务结束 | 同上 + exit_reason |
| `turn_before` | 每轮循环开始 | turn, messages, handler |
| `turn_after` | 每轮循环结束 | turn, messages, response, tool_results, next_prompt |
| `llm_before` | LLM 调用前 | messages, tools |
| `llm_after` | LLM 调用后 | response |
| `tool_before` | 工具执行前 | tool_name, args, response, index |
| `tool_after` | 工具执行后 | 同上 + ret (StepOutcome) |

---

## 2 个内置插件

### langfuse_tracing.py — Langfuse 可观测性

**自激活**：import 时检查 mykey.py 是否有 `langfuse_config`，有则初始化 `Langfuse(**cfg)`，无则 `_lf = None` 静默跳过。

**四层 span 上报**：
- `agent_before/after` → agent trace（记录 user_input → exit_reason）
- `llm_before/after` → generation span（记录 messages → response）
- `tool_before/after` → tool span（记录 tool_name, args → result）

**用途**：在 Langfuse Dashboard 观察 Agent 的每次任务、每轮 LLM 调用、每个工具执行的耗时和输入输出。

---

### project_mode.py — 项目模式（零核心改动）

**机制**：注册 `agent_before` 钩子，每轮将项目 L1 记忆注入到最后一条 user message。

**两层设计**：
- **L1（轻量）**：规则 + 记忆文件指针 + 收尾纪律，每轮全量注入
- **L2（按需）**：`project_memory.md` 全文不注入，Agent 按 L1 中的指针自行用 file 工具读取

**激活态管理**：
- 载体 = 文件锚 `temp/.active_project.<宿主pid>`
- PID 键控：多开 GA 各自激活不同项目，互不可见
- GA 关闭即自动失活（pid 变，旧锚作废）
- 启动时清扫旧版无后缀锚和自己 pid 的前世残留（不碰他进程的锚）

**目录约定**：
```
temp/projects/<项目名>/project_memory.md   项目记忆
temp/projects/<项目名>/                     项目私域文件
```

---

## 编写自定义插件

在 `plugins/` 下新建 `.py` 文件即可自动加载：

```python
# plugins/my_plugin.py
import plugins.hooks as hooks

@hooks.register('tool_after')
def log_tool_usage(ctx):
    tool_name = ctx.get('tool_name')
    print(f"[my_plugin] {tool_name} executed")
    # 无需 return，ctx 是引用，直接 mutate 即可

@hooks.register('llm_before')
def inject_context(ctx):
    messages = ctx.get('messages', [])
    # 在 LLM 调用前注入额外上下文
    # ...
```

文件以 `_` 开头则不会被自动加载（如 `_template.py`）。
