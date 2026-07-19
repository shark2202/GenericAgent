## 1. 前置流程清理（build 前必做）

- [x] 1.1 ~~推进或收尾 `add-selfextract-installer`~~ → supersede：comet 债清债在 dev 主仓完成（`add-llm-slash-cmd` 归档 + `2026-07-09-skill-lazy-load-mtime-cache` 真归档）。`add-selfextract-installer`（0/23 未实现、与协议无关）按用户决策接受为遗留 open，build 在软规则覆盖下进行（无硬 PreToolUse hook；selfextract yaml note 已记遗留决策，后续专门正规做）。阻塞解除。
- [x] 1.2 `hermes-isolated-skill-scorer` 确认无冲突：无 `.comet.yaml`（非 comet 托管），不构成源码写阻塞。

## 2. design 阶段 Design Doc（brainstorming 产出，解 design.md 的 Open Questions）

- [ ] 2.1 定版本化策略：semver 严格度 + capability negotiation 字段设计
- [ ] 2.2 定前向兼容策略：v2 server 是否仍服务 v1 client、deprecation 窗口
- [ ] 2.3 定确切消息 schema：每个 `type` 的 JSON 字段（initialize/ready/task/start/task/delta/tool/call/tool/result/task/done/task/interrupt/llm/list/llm/select/session/resume/approval/request/slash 转发）
- [ ] 2.4 定 Python stdio bridge 落点：新模块 `ga_stdio.py` vs 扩展 `agentmain --stdio` 模式
- [ ] 2.5 定 autonomous 生命周期建模：`mode`/`budget` 字段、budget 计量方式（token/turn/time）、自续触发语义
- [ ] 2.6 定 slash 转发消息形式：`slash/cmd` 消息 vs raw prompt 转发；复刻 `frontends/slash_cmds.py` 注入逻辑的范围
- [ ] 2.7 定契约测试范围：每 v1 能力的断言粒度

## 3. Python stdio bridge 参考实现

- [x] 3.1 实现 stdio 读写循环：逐行读 stdin JSON、逐行写 stdout JSON（带 id/type/version）
- [x] 3.2 实现 `initialize`/`ready` 握手 + capability 协商
- [x] 3.3 桥接 `GenericAgent.put_task` → 排空 `display_queue` → 发 `task/delta`/`tool/call`/`tool/result`/`task/done`（参考 `agentmain.py:229-235` item 形状 + `assets/ga_httpapp.py` 排空逻辑）
- [ ] 3.4 实现 `task/start` 返回 `task_id` + 多会话并发（多 put_task 并行排空）
- [ ] 3.5 实现 `task/interrupt` → `agent.abort()` → `task/done{reason:interrupted}`
- [ ] 3.6 实现 autonomous 生命周期：`task/start{mode:autonomous,budget}` → 持续 delta → budget 耗尽 `task/done{reason:budget}`（参考 `reflect/goal_mode.py` 自续）
- [ ] 3.7 实现 approval 人机回路：agent 发 `approval/request`，等 client 响应后继续
- [ ] 3.8 实现 MCP 可见性查询：经 `MCPClientManager.get_all_tools_summary()` 返回
- [ ] 3.9 实现 `llm/list`/`llm/select`（桥接 `agent.list_llms()`/`next_llm(n)`）+ `session/resume`（恢复 `llmclient.backend.history`）
- [ ] 3.10 实现 slash 命令转发：复刻 `slash_cmds.py` 注入逻辑（`/goal`/`/hive`/`/morphling`/`/conductor`/`/update`/`/autorun`）
- [ ] 3.11 实现错误韧性：畸形 JSON 回 error 不崩；child 侧不阻塞

## 4. 契约测试（tests/）

- [ ] 4.1 `test_protocol_transport.py`：握手成功 + 畸形 JSON 不崩（spec 传输 + E2）
- [ ] 4.2 `test_protocol_capability.py`：能力不匹配优雅退出（spec capability 协商）
- [ ] 4.3 `test_protocol_single_task.py`：单任务端到端流式（S1）
- [ ] 4.4 `test_protocol_multi_session.py`：双任务并发不串台（S2）
- [ ] 4.5 `test_protocol_interrupt.py`：中断运行中任务（S3）
- [ ] 4.6 `test_protocol_approval.py`：approval 闭环（S4）
- [ ] 4.7 `test_protocol_mcp_visibility.py`：MCP 可见性查询（S5）
- [ ] 4.8 `test_protocol_llm_session.py`：切模型 + 恢复会话（S6）
- [ ] 4.9 `test_protocol_autonomous.py`：autonomous 持续到 budget + 被中断（S7）
- [ ] 4.10 `test_protocol_slash_forward.py`：slash 转发注入正确 prompt + hive 多会话（S8/S9）
- [ ] 4.11 `test_protocol_resilience.py`：child 崩溃韧性（E1）+ 未初始化直接 task/start 被拒（E3）

## 5. verify 阶段

- [ ] 5.1 运行 `comet-state scale add-durable-agent-protocol` 定验证级别
- [ ] 5.2 全套契约测试 + 现有 199 pytest 无回归
- [ ] 5.3 ruff clean（新代码）+ 文档/MAP 更新
- [ ] 5.4 verify 通过 → archive 为稳定协议 v1
