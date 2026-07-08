---
okf_version: "0.1"
---

# Memory SOP 目录索引

> OKF v0.1 合规知识 bundle。本目录下所有非保留 `.md` 文件均有 YAML frontmatter + `type` 字段。

# SOP — 操作流程

* [自主行动 SOP](autonomous_operation_sop.md) — 自主行动模式下的报告路径、TODO 管理与执行流程规范
* [Checklist SOP](checklist_sop.md) — 启动者与执行者的清单驱动任务协作流程
* [computer_use](computer_use.md) — 屏幕视觉与坐标操作工具链的使用指南，关联 ui_detect.py 与 ljqCtrl
* [GitHub Contribution SOP](github_contribution_sop.md) — 给开源项目提 PR 的规范——一个 PR 做一件事、测试通过才推、尊重项目规范
* [Goal Hive Mode SOP](goal_hive_sop.md) — 多 agent 协作的 Hive 模式定义与执行流程，Master 调度 Worker 完成交付
* [Goal Mode SOP](goal_mode_sop.md) — 目标驱动模式的启用条件与执行流程
* [Incubator SOP](incubator_sop.md) — 自我复制到任意节点的 agent 网络部署流程，每个节点有独立记忆
* [ljqCtrl SOP](ljqCtrl_sop.md) — 物理坐标操作工具的使用规范——必须更新 checkpoint、禁用 pyautogui、操作前先激活窗口
* [MCP Client SOP](mcp_sop.md) — MCP Client 的使用流程——连接外部 MCP Server、调用工具、生命周期管理
* [记忆整理 SOP](memory_cleanup_sop.md) — 记忆层级间的整理与压缩流程，以存在性编码为核心原则
* [记忆管理 SOP](memory_management_sop.md) — L1-L4 记忆层级架构的职责定义、同步规则与核心公理
* [morphling SOP](morphling_sop.md) — Morphling 模式的定义与使用流程
* [Plan Mode SOP](plan_sop.md) — 计划模式的触发条件与执行流程——3步以上有依赖/多文件协同时启用
* [Memory Scanner SOP](procmem_scanner_sop.md) — 进程内存扫描工具的快速开始与使用流程
* [Project Mode SOP](project_mode_sop.md) — 项目模式的定义与执行流程
* [Review Mode SOP](review_sop.md) — In-session 对抗式代码审查模式，用 /review 触发
* [定时任务 SOP](scheduled_task_sop.md) — 定时任务的定义与执行规范
* [Subagent SOP](subagent.md) — 子 agent 的两种调用模式与使用规范
* [监察者模式 SOP](supervisor_sop.md) — 让用户一次说明任务后尽量不用多轮纠偏的监察者执行模式
* [TMWebDriver SOP](tmwebdriver_sop.md) — Chrome CDP 代理控制的特性与坑
* [UltraPlan SOP](ultraplan_sop.md) — UltraPlan 协议的启动与续行流程——复杂多步任务的规划与执行
* [验证 SOP](verify_sop.md) — 验证的两种失败模式——验证回避与假绿——及正确的验证执行规范
* [Vision API SOP](vision_sop.md) — 视觉 API 的前置规则与使用流程，含截图、OCR、图像理解
* [Vue 3 组件 SOP](vue3_component_sop.md) — Vue 3 组件的 JS 操作问题与解决方案
* [Web 工具链初始化 SOP](web_setup_sop.md) — web_scan 和 web_execute_js 工具链的初始化检查与设置流程

# Principle — 原则红线

* [什么是好的代码](code_review_principles.md) — 代码质量的四维判断标准——压缩性、局部性、可组合性、可证伪性

# Duty — 角色职责

* [Goal Hive Master 职责](goal_hive_master_duty.md) — Hive Master 的角色定义——拆解子任务、判断、汇总、调度 worker

# 子目录

* [autonomous_operation_sop/](autonomous_operation_sop/) — 自主行动模式的子文档
  * [任务规划模式](autonomous_operation_sop/task_planning.md) — 基于 TODO.txt 的任务规划与执行流程
* [review_sop/](review_sop/) — Review 模式的 inline prompt 文件（.txt，非 OKF 范围）
* [L4_raw_sessions/](L4_raw_sessions/) — 历史会话归档层（非 OKF 范围）
