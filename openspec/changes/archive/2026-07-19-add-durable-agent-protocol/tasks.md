## 1. 前置流程清理（build 前必做）

- [x] 1.1 ~~推进或收尾 `add-selfextract-installer`~~ → supersede：comet 债清债在 dev 主仓完成（`add-llm-slash-cmd` 归档 + `2026-07-09-skill-lazy-load-mtime-cache` 真归档）。`add-selfextract-installer`（0/23 未实现、与协议无关）按用户决策接受为遗留 open，build 在软规则覆盖下进行（无硬 PreToolUse hook；selfextract yaml note 已记遗留决策，后续专门正规做）。阻塞解除。
- [x] 1.2 `hermes-isolated-skill-scorer` 确认无冲突：无 `.comet.yaml`（非 comet 托管），不构成源码写阻塞。

## 2. design 阶段 Design Doc（brainstorming 产出，解 design.md 的 Open Questions）

- [x] 2.1 定版本化策略：semver 严格度 + capability negotiation 字段设计 <!-- design 阶段 Design Doc §4 定：LSP 风格 version="1" 主版本 + capabilities 数组协商 -->
- [x] 2.2 定前向兼容策略：v2 server 是否仍服务 v1 client、deprecation 窗口 <!-- Design Doc §4：v1 锁死 5 年，前向兼容靠 capabilities 协商 + 新 type 独立编号 -->
- [x] 2.3 定确切消息 schema：每个 `type` 的 JSON 字段（initialize/ready/task/start/task/delta/tool/call/tool/result/task/done/task/interrupt/llm/list/llm/select/session/resume/approval/request/slash 转发） <!-- Design Doc §5 消息 schema 表全覆盖 -->
- [x] 2.4 定 Python stdio bridge 落点：新模块 `ga_stdio.py` vs 扩展 `agentmain --stdio` 模式 <!-- Design Doc §6：新模块 ga_stdio.py，不改引擎入口 -->
- [x] 2.5 定 autonomous 生命周期建模：`mode`/`budget` 字段、budget 计量方式（token/turn/time）、自续触发语义 <!-- Design Doc §6.5：mode/budget，budget=turn 计量，桥接自续不 import reflect -->
- [x] 2.6 定 slash 转发消息形式：`slash/cmd` 消息 vs raw prompt 转发；复刻 `frontends/slash_cmds.py` 注入逻辑的范围 <!-- Design Doc §6.6：slash/cmd 消息 + prompt_for 源码 import -->
- [x] 2.7 定契约测试范围：每 v1 能力的断言粒度 <!-- Design Doc §8 测试策略：11 黑盒 wire 测试 S1-S9+E1-E3 -->

## 3. Python stdio bridge 参考实现

- [x] 3.1 实现 stdio 读写循环：逐行读 stdin JSON、逐行写 stdout JSON（带 id/type/version）
- [x] 3.2 实现 `initialize`/`ready` 握手 + capability 协商
- [x] 3.3 桥接 `GenericAgent.put_task` → 排空 `display_queue` → 发 `task/delta`/`tool/call`/`tool/result`/`task/done`（参考 `agentmain.py:229-235` item 形状 + `assets/ga_httpapp.py` 排空逻辑）
- [x] 3.4 实现 `task/start` 返回 `task_id` + 多会话并发（多 put_task 并行排空）
- [x] 3.5 实现 `task/interrupt` → `agent.abort()` → `task/done{reason:interrupted}`
- [x] 3.6 实现 autonomous 生命周期：`task/start{mode:autonomous,budget}` → 持续 delta → budget 耗尽 `task/done{reason:budget}`（参考 `reflect/goal_mode.py` 自续）
- [x] 3.7 实现 approval 人机回路：agent 发 `approval/request`，等 client 响应后继续
- [x] 3.8 实现 MCP 可见性查询：经 `MCPClientManager.get_all_tools_summary()` 返回
- [x] 3.9 实现 `llm/list`/`llm/select`（桥接 `agent.list_llms()`/`next_llm(n)`）+ `session/resume`（恢复 `llmclient.backend.history`）
- [x] 3.10 实现 slash 命令转发：复刻 `slash_cmds.py` 注入逻辑（`/goal`/`/hive`/`/morphling`/`/conductor`/`/update`/`/autorun`）
- [x] 3.11 实现错误韧性：畸形 JSON 回 error 不崩；child 侧不阻塞（实现已在 Task 1：`_parse_line` bad_json + `dispatch` not_initialized/bad_request 守卫 + serve stdin EOF 自然退出；§4.1/§4.2/§4.11 wire 测试验证）

## 4. 契约测试（tests/）

- [x] 4.1 `test_protocol_transport.py`：握手成功 + 畸形 JSON 不崩（spec 传输 + E2）
- [x] 4.2 `test_protocol_capability.py`：能力不匹配优雅退出（spec capability 协商）
- [x] 4.3 `test_protocol_single_task.py`：单任务端到端流式（S1）
- [x] 4.4 `test_protocol_multi_session.py`：双任务并发不串台（S2）
- [x] 4.5 `test_protocol_interrupt.py`：中断运行中任务（S3）
- [x] 4.6 `test_protocol_approval.py`：approval 闭环（S4）
- [x] 4.7 `test_protocol_mcp_visibility.py`：MCP 可见性查询（S5）
- [x] 4.8 `test_protocol_llm_session.py`：切模型 + 恢复会话（S6）
- [x] 4.9 `test_protocol_autonomous.py`：autonomous 持续到 budget + 被中断（S7）
- [x] 4.10 `test_protocol_slash_forward.py`：slash 转发注入正确 prompt + hive 多会话（S8/S9）
- [x] 4.11 `test_protocol_resilience.py`：child 崩溃韧性（E1）+ 未初始化直接 task/start 被拒（E3）

## 5. verify 阶段

- [x] 5.1 运行 `comet-state scale add-durable-agent-protocol` 定验证级别 <!-- DONE：35 tasks / 1 delta spec / 22 files → verify_mode=full -->
- [x] 5.2 全套契约测试 + 现有 199 pytest 无回归 <!-- DONE：fix subagent 282 passed / 0 skipped / 0 failed (GA_HAS_LLM=1 pytest, 177s)；199 baseline 无回归 -->
- [x] 5.3 ruff clean（新代码）+ 文档/MAP 更新 <!-- DONE：ga_stdio.py + test_protocol_transport.py ruff clean；MAP.md 加 ga_stdio.py 条目；13 pre-existing ruff errors 在未触测试代码 defer-verify -->
- [x] 5.4 verify 通过 → archive 为稳定协议 v1 <!-- build-complete 里程碑：实现+11 wire 测试+final review(Approved) 全 DONE，移交 verify 阶段实质复核 (comet-state scale ✅ + verification-before-completion + verify guard) 后 archive -->
