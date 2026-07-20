## 1. 前置与脚手架

- [ ] 1.1 Rust crate 脚手架：`ga-engine/`（或复用 `ga_rust/`），Cargo.toml 依赖（reqwest + tokio + rmcp + serde + serde_yaml + 基础 crates），`#[tokio::main]` 入口，`python -m ga_stdio` 等价的 `ga-engine` 二进制
- [ ] 1.2 确认与 `add-durable-agent-protocol`（已归档）的 durable protocol v1 契约 + 11 wire 测试作为对齐基座（无源码写阻塞）
- [ ] 1.3 全量扫描 `memory/` 找所有依赖 `inline_eval` 隐式 eval 引擎对象的 SOP（已识别 `autonomous_operation_sop.md`/`plan_sop.md` 2 处，须找全）+ 记录改写映射表

## 2. stdio 协议层（阶段 0）

- [ ] 2.1 Rust stdio 读写循环：逐行读 stdin JSON、逐行写 stdout JSON（带 id/type/version）
- [ ] 2.2 `initialize`/`ready` 握手 + capability 协商（version="1" + capabilities 数组）
- [ ] 2.3 错误韧性：畸形 JSON 回 error 不崩、未初始化拒业务消息、stdin EOF 自然退出
- [ ] 2.4 对齐验证：跑 `test_protocol_transport`（§4.1）+ `test_protocol_resilience`（§4.11）wire 测试

## 3. agent_loop + llmcore + 核心工具（阶段 1）

- [ ] 3.1 agent_loop Rust 实现：tokio task + mpsc::Sender 推事件（替代 Python 生成器 yield），event channel schema 设计
- [ ] 3.2 llmcore NativeClaudeSession：async reqwest 手撸，Anthropic 原生 tool-use，保 prompt-caching-scope beta / `?beta=true` / thinking_type 自定义 header
- [ ] 3.3 llmcore NativeOAISession：OpenAI 原生 tool-use，model 名含 claude/anthropic 时的特殊处理
- [ ] 3.4 MixinSession：多 session 故障转移 + spring-back 状态机（`_cur_idx`/`_switched_at`/`_spring_sec` 跨 Native 统一）
- [ ] 3.5 history 管理：trim/compress/fix messages 原样复刻
- [ ] 3.6 核心外部工具 Rust 原生：file_read/file_write/file_patch/code_run（subprocess，支持 python/其它 code_type + timeout + 截断）
- [ ] 3.7 引擎能力工具编译期注册：register_done_hook / enter_plan_mode / get_history / get_memory_state（inline_eval 替代品）
- [ ] 3.8 对齐验证：跑 `test_protocol_single_task`（§4.3 S1）wire 测试

## 4. task/interrupt（阶段 2）

- [ ] 4.1 task/interrupt → abort → task/done{reason:interrupted}（含 queued interrupt：ctx 未占槽位移出队列 + done{interrupted}，复刻 I1 fix）
- [ ] 4.2 对齐验证：跑 `test_protocol_interrupt`（§4.5 S3）

## 5. 多会话并发（阶段 3）

- [ ] 5.1 per-task GA 实例池 + tokio 有界并发（semaphore max 4，env GA_STDIO_MAX_CONCURRENCY）+ FIFO 排队 + 槽位移交
- [ ] 5.2 对齐验证：跑 `test_protocol_multi_session`（§4.4 S2）

## 6. MCP 可见性+调用（阶段 4）

- [ ] 6.1 rmcp 客户端集成：连接 MCP server + `tools/list` 可见性 + `tools/call` 调用
- [ ] 6.2 mcp/list 协议消息：经 rmcp 返回 servers + tools 结构
- [ ] 6.3 外部用户工具 = MCP server：drop-in 发现（扫 tools/ 目录的 stdio MCP server + manifest）
- [ ] 6.4 对齐验证：跑 `test_protocol_mcp_visibility`（§4.7 S5）

## 7. LLM 切换+会话恢复（阶段 5）

- [ ] 7.1 llm/list + llm/select（桥接 next_llm(n)）
- [ ] 7.2 session/resume（恢复 backend.history 续聊）
- [ ] 7.3 对齐验证：跑 `test_protocol_llm_session`（§4.8 S6）

## 8. approval 人机回路（阶段 6）

- [ ] 8.1 approval/request 发出 + agent 线程阻塞 + approval/response 唤醒（ask_user 引擎能力工具）
- [ ] 8.2 对齐验证：跑 `test_protocol_approval`（§4.6 S4）

## 9. skill + L1-L4 内存（阶段 7）

- [ ] 9.1 skill_loader：SKILL.md + frontmatter 扫描（serde_yaml）+ 技能发现 + mtime 缓存
- [ ] 9.2 get_global_memory：L1/L2 文本拼接 prompt + L3 结构导航
- [ ] 9.3 sync_skills_to_l1：技能索引写回 L1
- [ ] 9.4 get_skill_detail / skill_query 引擎能力工具
- [ ] 9.5 memory 目录与 Python 版兼容验证（同一 memory/ 目录双向可用）
- [ ] 9.6 新建 wire 测试：skill 查询 + memory 拼接 + 对齐

## 10. hermes 自进化（阶段 8）

- [ ] 10.1 distill worker task + channel：post-task（agent_after hook）推 history 快照 + catalog，不阻塞主循环
- [ ] 10.2 Scorer trait + InProcessScorer（本地 JSON 校验）+ SubagentScorer（调子 LLM 打分）
- [ ] 10.3 skill_manage 引擎内置工具：CRUD（create/patch/retire/list_evolvable）+ GA_SKILL_EVOLUTION_ENABLED 门控
- [ ] 10.4 熔断：MAX_AUTO_PATCH_PER_SKILL=5 + MAX_CHANGES_PER_DISTILL=3 原样
- [ ] 10.5 新建 wire 测试：post-task distill 触发 + 打分闸门 + 熔断 + 对齐
- [ ] 10.6 MVP 发版：阶段 0-8 全 wire 测试绿 + 分发验证（Rust 单二进制，无 PBS bundle）

## 11. MVP 后（阶段 9-11，留后续）

- [ ] 11.1 autonomous 自续循环（阶段 9）：task/start{mode:autonomous,budget} + budget{seconds,turns} + task/done{reason:budget|interrupt}（§4.9 S7）
- [ ] 11.2 slash 转发（阶段 10）：slash/cmd + prompt 注入 + 状态类 raw 转发（§4.10 S8/9）
- [ ] 11.3 切默认 + Python 退役（阶段 11）：全 11 wire 测试 + 等价 199 pytest；Python 版退役策略（切默认即归档 vs 长期双轨）待定
