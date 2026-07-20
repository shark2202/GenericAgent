## Context

GA 引擎当前全 Python（核心引擎 ~5600 LOC：agentmain/agent_loop/ga/ga_utils/llmcore/mcp_client/ga_stdio；总 50k LOC 含 frontends/memory/tools）。分发捆 PBS 可移植 CPython（archive 149MB / installer 96MB），`assemble-dist-local.sh` 逐文件 cp 14215 个 PBS 文件在 Windows+杀毒下 ~50 分钟（已验证）。

刚冻结的 durable agent protocol v1（`add-durable-agent-protocol` 已归档）定义了 stdio JSON-RPC 契约 + 11 wire 测试。Python 版 `ga_stdio.py` 已实现该协议。这为引擎替换提供了中立边界：前端经协议调用引擎，引擎可换而不动前端。

本设计是 13 轮 grilling 共识的固化。深度技术细化（Rust 模块切分、trait 设计、channel schema、SSE 解析细节）留 design 阶段 Design Doc。

## Goals / Non-Goals

**Goals:**
- 把 GA 引擎迁到 Rust，产出**单二进制（无 Python runtime）**，根治分发复杂。
- MVP（阶段 0-8）功能等价于 Python GA 核心 + 自进化：stdio 协议 / agent_loop / llmcore(Native) / 核心工具 / 中断 / 多会话并发 / MCP / LLM 切换+会话恢复 / approval / skill+L1-L4 内存 / hermes 自进化。
- 经 durable protocol v1 双轨共存，前端无感切引擎，Python 版兜底。
- 逐阶段用 durable protocol 的 wire 测试对齐 Python 行为，风险分散。

**Non-Goals:**
- 不做 Rust CLI/TUI/GUI 前端（前端经 durable protocol 不变；前端迁 Rust 是后续 change）。
- 不改 Python 版源码（双轨共存）。
- autonomous 自续循环（阶段 9）/ slash 转发（阶段 10）/ Python 版退役（阶段 11）留 MVP 后。
- 不内嵌 Python（PyO3/embed）——会重新引入 Python runtime，违背单二进制。
- 不迁 frontends 16 个 Python 前端（留 MVP 后单独决策）。

## Decisions

13 项 grilling 决策（含否决的备选 + 理由）：

1. **目标边界 = 渐进迁移引擎到 Rust**（非"Rust 前端+Python 引擎"、非 strangler FFI）。前者不解决分发痛点；后者 PyO3 拉回 Python runtime。
2. **MVP 能力面 = 核心引擎 + L1-L4 + hermes 自进化 + MCP + skill**。用户明确必须含。
3. **内存存储 = 文本文件 + prompt 拼接原样复刻**（非 SQLite/结构化）。memory 内容价值在兼容，Rust 易复刻，与 Python 版共享 memory 目录。
4. **memory 里 .py 工具 = 用户环境准备，Rust 不内嵌 Python**。memory .py 不在工具 dispatch 路径（是用户侧脚本，经 code_run 间接跑）。
5. **inline_eval = 砍进程内 eval，subprocess + 显式工具替代**（非内嵌 Python 保 inline）。保单二进制；2 处 SOP（`_done_hooks`/`enter_plan_mode`）改写为 `register_done_hook`/`enter_plan_mode` 工具。
6. **工具分层 = 引擎能力 vs 外部工具收敛**（非 1:1 全迁）。引擎能力走强类型 API + 编译期注册；外部工具 Rust 原生。修设计债。
7. **工具扩展 = 核心编译期 + 外部 subprocess 协议**（非内嵌解释器、非纯编译期）。单二进制 + 开放扩展。
8. **外部工具协议 = 统一 MCP server，复用 MCP 协议**（非自定义 manifest/单行协议）。复用本就要迁的 mcp_client + MCP 标准 + 跨语言；drop-in = stdio MCP server 脚本。
9. **llmcore 覆盖 = 只迁 Native，砍传统手写解析**。传统 Session 是死代码（mykey 模板只配 Native）；砍掉 SSE 分片重组一半迁移量。
10. **llmcore 技术栈 = 手撸 reqwest+tokio**（非 SDK）。GA 有自定义 beta header（prompt-caching-scope / `?beta=true` / thinking_type），SDK 不支持；MixinSession 跨 SDK 统一极难。
11. **并发/异步 = 全 tokio async**（非全同步线程、非混合）。MCP SDK（rmcp）是 async，同步会被 MCP 拖垮；agent_loop 生成器→mpsc::Sender channel 重构。
12. **hermes 自进化 = distill worker+channel + trait Scorer + skill_manage 引擎内置**。post-task 推快照不阻塞主循环；Protocol→trait；skill_manage 调内部 distill 故归引擎能力层。
13. **过渡共存 = 渐进双轨，durable protocol 做枢纽**（非 big-bang、非 strangler）。协议中立，前端无感切引擎，无 FFI，Python 兜底。

## Risks / Trade-offs

- **[llmcore Native SSE 手撸]** → 用 Python 版 SSE 响应做 golden snapshot 对比；自定义 beta header 逐个对齐。
- **[agent_loop 生成器→channel 重构]** → 先定义 event channel schema 再写；Python yield 语义用 mpsc::Sender 等价。
- **[inline_eval SOP 改写]** → 全量扫 memory/ 找所有隐式 eval 引擎对象的 SOP；已识别 2 处，须找全。
- **[hermes distill worker 编排]** → post-task 触发时机 + history 快照一致性用 channel + 快照拷贝保证。
- **[MVP 工作量大]**（阶段 0-8 ≈ 5600 LOC Rust 核 + hermes/memory + 新 wire 测试）→ 发版晚但双轨共存保证 Python 版全程可用，不阻塞现网。
- **[Breaking 分歧]**（drop-in→MCP / 传统 Session 砍 / inline_eval 砍）→ 双轨期 Python 版不受影响；Rust 版切默认前提供迁移文档 + 工具改写指引。
- **[MCP 作为外部工具协议的重量]** → 用户写简单工具也要按 MCP server；缓解：最小 stdio MCP server 是一个读 stdin 写 stdout 的脚本，且能复用社区 MCP server。

## Migration Plan

双轨接管路线（11 阶段，每阶段 wire 测试对齐 = 可切 Rust 引擎该能力）：

0. stdio 协议+握手+韧性（§4.1 transport + §4.11 resilience）
1. agent_loop + llmcore(Native) + 核心工具（§4.3 S1）
2. task/interrupt（§4.5 S3）
3. 多会话并发（§4.4 S2）
4. MCP 可见性+调用（§4.7 S5）
5. LLM 切换+会话恢复（§4.8 S6）
6. approval 人机回路（§4.6 S4）
7. skill + L1-L4 内存（新建 wire 测试）
8. hermes 自进化（新建 wire 测试）
—— MVP 发版边界 ——
9. autonomous 自续（§4.9 S7）
10. slash 转发（§4.10 S8/9）
11. 切默认 + Python 退役（全 11 wire 测试 + 等价 199 pytest）

每阶段：Rust 版跑通对应 wire 测试 → 该能力对齐 Python → 前端经 durable protocol 切 Rust 引擎该能力（无感）。阶段 11 全绿切默认引擎为 Rust、Python 版退役（退役策略 MVP 后定）。
