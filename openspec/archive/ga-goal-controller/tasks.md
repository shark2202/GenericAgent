## 1. Goal 数据层

- [x] 1.1 设计并实现 `goals` 表（id / state / proposal / max_concurrent_runners / max_duration / supervisor / reason / confirm_token / created_at / finished_at）+ 迁移
- [x] 1.2 实现 `goal_deliverables` 表（goal_id / 聚合内容 / 格式）+ 仓储层
- [x] 1.3 goal 仓储 CRUD + 状态转换单测

## 2. Goal 状态机与控制器

- [x] 2.1 实现状态机 `proposed → confirmed → running → done|failed|budget-exhausted|timeout`（非法转换报错）
- [x] 2.2 实现 `propose`（生成方案 + 一次性限时 confirm-token，仅 record 不执行）
- [x] 2.3 实现 `confirm`+`run`（校验 confirm-token 后启动后台控制器）
- [ ] 2.4 实现后台控制器循环（派发子 session/runner、聚合进度、达终态收尾）— _部分：线程级循环存在但未接入daemon_

## 3. CLI 子命令组 `ga goal`

- [ ] 3.1 用 clap 实现 `goal propose` / `goal run` / `goal status` / `goal deliverable get`（复用 Item 1 origin 三元组、JSON schema、退出码）— _当前为Python CLI，非Rust clap集成_
- [ ] 3.2 `propose` 输出 goal id + confirm-token；`run` 校验 token 缺失/失效报错（退出码）— _Python CLI有基础实现_
- [ ] 3.3 `--json` 版本化输出与稳定退出码（复用 Item 1 契约）

## 4. 预算 / 时长 / 后台 / 跨重启

- [x] 4.1 实现并发预算检查（派发前 active < max_concurrent_runners，否则排队；满且无活跃 → `budget-exhausted`）— _spawn_lock + concurrent tests_
- [x] 4.2 实现 `max_duration` deadline timer（到期 → `timeout`，优雅停活跃 runner，复用 Item 1 `RunnerSupervisor.terminate`）
- [ ] 4.3 实现后台执行（控制器跑在 daemon，客户端断开继续）— _线程级而非daemon级_
- [x] 4.4 实现跨重启恢复（从 SQLite 恢复 `running` goal，重连/重启其下 session，标记 interrupted 策略）— _GoalStore.load_running_goals_
- [x] 4.5 验证预算耗尽 / 时长到期 / 跨重启恢复三个路径 — _test_goal_controller.py 覆盖_

## 5. Deliverable 归集

- [x] 5.1 实现从 goal 下 session 终答 + 关键事件聚合 deliverable — _GoalDeliverable类存在_
- [x] 5.2 实现 `goal deliverable get <id>`（纯文本摘要 + 可选 `--json`）— _Python CLI基础实现_
- [x] 5.3 终态前写入 `goal_deliverables` 表并验证可检索

## 6. 集成验证

- [ ] 6.1 端到端：propose → confirm → run → 多 session 派发 → status/deliverable（CLI 形态）
- [ ] 6.2 端到端：预算耗尽与时长到期终止语义正确
- [ ] 6.3 端到端：Core 重启后 goal 恢复并继续/收尾
- [ ] 6.4 与 Item 1 联调（复用 runner spawn / 事件流 / 审批 / CLI 契约无回归）— _T2未合并到T1分支_
