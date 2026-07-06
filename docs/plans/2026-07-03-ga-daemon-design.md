# GA Daemon — golang 单二进制网关设计方案

**日期**: 2026-07-03
**状态**: 设计完成，待实现

## 目标

将 GenericAgent 包装为 golang 编译的单二进制 daemon（`ga`），通过 JSON-RPC over WebSocket 对外暴露 API，实现零依赖分发与远程调用。支持内置 GA（go:embed 打包）和外部 GA（`--ga-path` 引用已有环境）双模式。

## 架构总览

```
ga (golang 单二进制 ~80-150MB)
├─ ga server           常驻 daemon (子进程管理 + WS server)
│   ├─ 内置 GA 模式    go:embed CPython + GA 核心 + deps → 解压到 $GA_HOME/runtime/
│   └─ 外部 GA 模式    --ga-path /path/to/GenericAgent (只读，不触碰用户代码)
├─ ga session new      新建会话，连 daemon WS
├─ ga session list     列出会话
├─ ga session watch    流式追尾输出
├─ ga session close    关闭会话
├─ ga session archive  归档会话
├─ ga llm list         列出模型
├─ ga llm switch       切换模型
├─ ga status           daemon 状态
└─ ga version          版本信息
```

### 进程模型

- **每会话一进程**：`session.create` 时 fork 一个 python 子进程跑 `ga_shim.py`，常驻支持多轮
- **崩溃隔离**：单 session 子进程崩溃不影响其他 session 或 daemon
- **共享 memory**：所有 session 共用 `$GA_HOME/memory/`（L0-L4 长期记忆）

### $GA_HOME 目录布局

```
$GA_HOME/                          (默认 ~/.ga，环境变量可覆盖)
├─ runtime/<arch>/python/          # go:embed 解压出的可移植 CPython + deps
├─ code/                           # GA 核心代码 (内置模式)
├─ memory/                         # L0–L4 长期记忆（跨 session 共享）
├─ temp/                           # 工作区 (code_run cwd)
├─ logs/                           # model_responses 日志
└─ sessions/
    └─ <session_id>/
        ├─ history.json            # 完整对话快照
        ├─ working.json            # key_info checkpoint
        └─ meta.json               # created_at, llm_no, model, status
```

## ga_shim.py（新增，~80 行）

放在 GA 代码根目录，随 go:embed 打包。不改动任何现有 .py 文件。

**职责**：
- stdin 读 JSON lines 请求（prompt / cancel）
- 用 `GenericAgent.put_task()` + `display_queue` 驱动 agent
- stdout 写 JSON lines 事件（stream.chunk / stream.done / ask_user.request）
- turn_end 时增量写 working.json，任务完成时写 history.json

**ask_user 闭环**：shim 检测到 INTERRUPT → 发 `ask_user.request` notification → 回到 stdin 等待 → client 回复作为新 prompt 投递 → agent 继续。

## JSON-RPC API

**协议**：JSON-RPC 2.0 over WebSocket。request 有 id（等响应），notification 无 id（单向推送）。

### Client → Server (request)

| method | params | result | 说明 |
|--------|--------|--------|------|
| `session.create` | `{resume_from?, llm_no?, ga_path?}` | `{session_id}` | fork python 子进程 |
| `session.close` | `{session_id}` | `{ok}` | 发 cancel → SIGTERM |
| `session.list` | `{}` | `[{session_id, status, model, created_at}]` | 列活跃 session |
| `message.send` | `{session_id, content, images?}` | `{message_id}` | 投递 prompt 或回复 ask_user |
| `stream.cancel` | `{session_id}` | `{ok}` | 中止当前任务 |
| `llm.list` | `{}` | `[{no, name, active}]` | 透传 `agent.list_llms()` |
| `llm.switch` | `{session_id, llm_no}` | `{name}` | 透传 `agent.next_llm()` |

### Server → Client (notification)

| method | params | 说明 |
|--------|--------|------|
| `stream.chunk` | `{session_id, message_id, delta, turn}` | 流式增量 |
| `stream.done` | `{session_id, message_id, full, turn}` | 任务完成，触发 history 落盘 |
| `ask_user.request` | `{session_id, message_id, question, candidates?}` | 等待 client 回复 |
| `session.event` | `{session_id, type, detail?}` | crashed / closed / llm_changed |

## golang 内部结构

```
ga/
  main.go              # 子命令分发 (server / session / llm / status)
  cmd/
    server.go          # daemon 启动，WS server 监听
    session.go         # session 子命令 (new/list/watch/close/archive)
    llm.go             # llm 子命令 (list/switch)
  internal/
    runtime.go         # 首次解压 go:embed → $GA_HOME/runtime/
    session_mgr.go     # sync.Map[session_id -> *Session]
    session.go         # *Session 结构体 (cmd, stdin, stdout scanner, msg_id, mutex)
    ws.go              # gorilla/websocket handler
    jsonrpc.go         # JSON-RPC 2.0 编解码、method 路由、schema 校验
    embed.go           # //go:embed dist/python-bundle/* ga_shim.py agentmain.py ga.py ...
    api.schema.json    # 所有 method 的 JSON Schema (编译时 go:embed 供校验)
```

### 并发模型

- 全局：session_mgr 清理协程，每秒扫描 `cmd.Wait()` 返回的 session
- 每 session：stdout reader goroutine，阻塞读行，分发到 WS
- 每 WS 连接：handler goroutine，读 client message → dispatch → 等 response

### 双模式

| | 内置 GA（默认） | 外部 GA（--ga-path） |
|---|---|---|
| python | `$GA_HOME/runtime/<arch>/python` | 外部 GA 的 python 或系统 python |
| GA 代码 | go:embed 解压到 `$GA_HOME/code/` | 直接读 `$GA_PATH/` |
| memory/ | `$GA_HOME/memory/` | `$GA_PATH/memory/`（只读） |
| mykey.py | `$GA_HOME/mykey.py` | `$GA_PATH/mykey.py`（只读） |
| 写入 | 自由写内置 code | **只读**，不触碰外部代码/memory/SOP |

外部 GA 模式下，session 数据写入 `$GA_HOME/sessions/`，不污染外部目录。

## 构建流程

```
Step 1: scripts/bundle-python.sh
  → dist/python-bundle/<arch>/python/       (可移植 CPython + pip deps)

Step 2: go generate
  → go:embed dist/python-bundle/* + ga_shim.py + agentmain.py + ga.py
    + memory/ + assets/ + llmcore.py + agent_loop.py + ...

Step 3: go build -ldflags "-X main.version=v0.1.0" -o ga .
  → ga (单二进制)

Step 4: (CI) 矩阵构建 win-x64 / mac-arm64 / linux-x64
  → GitHub Releases
```

## 使用方式

```bash
# 首次运行自动解压
./ga server start                          # 内置 GA 模式
./ga server start --ga-path ~/GenericAgent # 外部 GA 模式

# 会话操作
./ga session new "帮我查一下天气"
./ga session list
./ga session watch <id>                    # 流式追尾
./ga session close <id>
./ga session archive <id>

# 模型管理
./ga llm list
./ga llm switch <id> <n>

# 状态
./ga status
./ga version
```

## 与现有代码的关系

- **不改动** agentmain.py、ga.py、agent_loop.py、llmcore.py 等任何现有 .py 文件
- **新增** ga_shim.py (~80 行)，用已存在的 `GenericAgent.put_task()` + `display_queue` SDK API
- **新增** golang 项目目录 `ga/`
- 与现有前端（TUI/Streamlit/IM Bot）并存，不替代，是新增的 daemon 入口

## 与 Galley 的定位差异

| | Galley | ga-server |
|---|---|---|
| 语言 | Rust + Tauri + React | 纯 golang |
| 前端 | 自带 GUI | 无 GUI，纯 API 网关 + CLI |
| 编排 | Project/Goal/Supervisor 内置 | 不做，留给客户端 |
| 持久化 | SQLite + FTS5 全文搜索 | JSON 文件 |
| 定位 | GA 的完整工作台 | GA 的 LSP daemon — 更底层、更通用 |