---
comet_change: integrate-skills-with-memory
role: technical-design
canonical_spec: openspec
---

# Design Doc: Skill 与 L1-L4 记忆体系集成

## 1. 背景与现状

### 1.1 项目核心架构

GA 的核心是 L1-L4 记忆 + 自进化闭环：

```
L0  memory_management_sop.md        ← 宪法（记忆管理元规则）
L1  global_mem_insight.txt (≤30行)   ← 索引层（路由表），每轮注入
L2  global_mem.txt                   ← 事实库
L3  memory/*.md | *.py              ← 任务SOP + 可执行工具，按需 file_read
L4  L4_raw_sessions/                 ← 原始会话历史
```

自进化闭环：任务执行 → L1 路由 → L3 SOP 指导 → 工具验证 → `do_start_long_term_update` → Agent `file_patch` 更新 L1/L2/L3 → 新记忆可被未来 L1 路由到。

### 1.2 现有 Skill 支持的问题

`skill_loader.py` 的 `get_skill_catalog()` 将 Skill catalog 全量追加到 `agentmain.py:46` 的 system_prompt 末尾。问题：

| 问题 | 影响 |
|---|---|
| Skill 不在 L1 索引中 | 自进化系统不可见，无法路由 |
| 全量 description 注入 | token 随 Skill 数量线性膨胀，违背 L1 ≤30 行哲学 |
| 无经验回写路径 | Skill 使用经验无法通过自进化写入记忆 |
| 无使用追踪 | 工作记忆 `related_sop` 不追踪 Skill |
| 与 L3 SOP 割裂 | 两套平行知识体系，信任边界混淆 |

## 2. 设计目标

### Goals
- Skill 作为 L3 独立子类接入 L1 路由
- `skill_loader.py` 自动同步 Skill 到 L1，Agent 可微调
- Skill 使用经验通过标准自进化路径回写
- 工作记忆复用，Skill 使用跨任务继承
- Token 预算对齐 L1 哲学

### Non-Goals
- 不重新设计 L1-L4 记忆系统本身
- 不改变 `.agents/skills/` 目录结构
- 不修改 `agent_loop.py`
- 不构建 Skill 市场/远程分发

## 3. 架构设计

### 3.1 整体数据流

```
                          ┌─────────────────────────┐
                          │  .agents/skills/*/SKILL.md │  (外部作者)
                          └────────────┬────────────┘
                                       │ sync_skills_to_l1()
                                       ▼
┌──────────────────────────────────────────────────────────┐
│  memory/global_mem_insight.txt (L1)                      │
│  ...                                                     │
│  <!-- auto-skills-start -->                              │
│  comet-open: /abs/path/SKILL.md | memory/skill_exp_comet-open.md
│  comet-design: /abs/path/SKILL.md                       │
│  <!-- auto-skills-end -->                                │
│  # Agent 手动备注区（不受 sync 影响）                      │
│  ...                                                     │
└──────────────────────────┬───────────────────────────────┘
                           │ get_global_memory() 每轮注入
                           ▼
                    system_prompt
                           │
                           ▼
                    Agent 决策 → file_read SKILL.md
                           │
                    任务执行 → 验证
                           │
                    do_start_long_term_update
                           │
                    Agent file_patch → memory/skill_exp_<name>.md
                           │
                    下次 sync → L1 条目自动追加经验文件路径
```

### 3.2 模块改动

#### 3.2.1 skill_loader.py — 核心重写

**旧接口**：`get_skill_catalog() -> str`（返回格式化 catalog 字符串）

**新接口**：`sync_skills_to_l1(l1_path: str = None) -> None`（同步 Skill 到 L1 文件）

**内部流程**：

```python
def sync_skills_to_l1(l1_path=None):
    """
    扫描 Skill 目录 → 同步到 L1 [Skills] 段（auto marker 之间）。
    保留 marker 外的手动备注。
    """
    l1_path = l1_path or os.path.join(script_dir, 'memory/global_mem_insight.txt')
    
    # 1. 扫描 Skill（复用现有 _scan_dir 逻辑）
    catalog = _discover_skills()  # {name: (desc, skill_md_path)}
    
    # 2. 检测经验文件
    entries = []
    for name, (desc, skill_path) in sorted(catalog.items()):
        exp_path = os.path.join(script_dir, f'memory/skill_exp_{name}.md')
        if os.path.isfile(exp_path):
            entries.append(f"{name}: {skill_path} | {exp_path}")
        else:
            entries.append(f"{name}: {skill_path}")
    
    # 3. 构建 auto 段内容
    auto_block = "<!-- auto-skills-start -->\n"
    if entries:
        auto_block += "\n".join(entries) + "\n"
    auto_block += "<!-- auto-skills-end -->"
    
    # 4. 读写 L1：替换 marker 之间内容，保留外部
    _replace_between_markers(l1_path, 
                             '<!-- auto-skills-start -->', 
                             '<!-- auto-skills-end -->', 
                             auto_block)
```

**`_replace_between_markers` 实现**：

```python
def _replace_between_markers(filepath, start_marker, end_marker, new_content):
    """替换文件中 start_marker ~ end_marker 之间的内容，保留外部。"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    
    if start_idx == -1:
        # marker 不存在 → 追加到文件末尾
        if content and not content.endswith('\n'):
            content += '\n'
        content += new_content + '\n'
    else:
        if end_idx == -1:
            end_idx = len(content)
        else:
            end_idx += len(end_marker)
        content = content[:start_idx] + new_content + content[end_idx:]
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
```

**保留的函数**：`_parse_skill_frontmatter`、`_scan_dir` 不变（Skill 发现逻辑复用）。

**移除的函数**：`get_skill_catalog()`（catalog 格式化逻辑删除）。

#### 3.2.2 agentmain.py — 移除 catalog 调用

```python
# 旧（line 42-47）:
def get_system_prompt():
    with open(...) as f: prompt = f.read()
    prompt += f"\nToday: {time.strftime('%Y-%m-%d %a')}\n"
    prompt += get_global_memory()
    prompt += get_skill_catalog()  # ← 移除此行
    return prompt

# 新:
def get_system_prompt():
    with open(...) as f: prompt = f.read()
    prompt += f"\nToday: {time.strftime('%Y-%m-%d %a')}\n"
    prompt += get_global_memory()
    return prompt
```

#### 3.2.3 ga.py — sync 集成到 get_global_memory()

```python
# 旧（line 583-594）:
def get_global_memory():
    prompt = "\n"
    try:
        with open('memory/global_mem_insight.txt', ...) as f: insight = f.read()
        # ...
        prompt += insight + "\n"
    except FileNotFoundError: pass
    return prompt

# 新:
def get_global_memory():
    prompt = "\n"
    try:
        sync_skills_to_l1()  # ← 新增：sync 后再读 L1
        with open('memory/global_mem_insight.txt', ...) as f: insight = f.read()
        # ...
        prompt += insight + "\n"
    except FileNotFoundError: pass
    return prompt
```

**sync 时机分析**：
- `get_global_memory()` 在 `get_system_prompt()` 中调用（每任务启动）
- `turn_end_callback` 中 `turn % 10 == 0` 重新注入（ga.py:569）
- 因此 sync 在每任务启动 + 每 10 轮时触发，覆盖热加载需求

#### 3.2.4 工作记忆 — 无需改动

`related_sop` 是 Agent 自主写入的工作记忆字段（`do_update_working_checkpoint`）。Agent 使用 Skill 时自然会通过 `file_read` 读取 SKILL.md，路径会出现在 `related_sop` 中。跨任务继承机制（`agentmain.py:159-163`）已存在，无需改动。

#### 3.2.5 自进化 — 无需改动

`do_start_long_term_update`（ga.py:508）返回 L0 规则 + 提取 prompt，指导 Agent 更新 L1/L2/L3。Skill 经验文件属于 L3，Agent 通过 L1 `[Skills]` 段看到经验文件路径后，自然走标准 `file_patch` 写入路径。无需修改该工具。

#### 3.2.6 可选增强：版本备份

```python
# skill_loader.py 新增
import shutil

def backup_and_patch_skill(skill_md_path, patch_content):
    """
    opt-in: 备份 SKILL.md 原版后 patch。
    需 GA_SKILL_PATCH_ENABLED=1 环境变量。
    """
    if os.environ.get('GA_SKILL_PATCH_ENABLED') != '1':
        raise PermissionError("Skill patch requires GA_SKILL_PATCH_ENABLED=1")
    
    bak_path = skill_md_path + '.bak'
    if not os.path.exists(bak_path):
        shutil.copy2(skill_md_path, bak_path)
    
    # Agent 通过 file_patch 修改 SKILL.md
    # 此函数仅负责备份，patch 由 Agent 的 file_patch 工具执行
```

**注意**：此函数仅负责备份。实际 patch 由 Agent 的 `file_patch` 工具执行。Agent 调用此函数备份后，再用 `file_patch` 修改 SKILL.md。

### 3.3 L1 [Skills] 段格式

```
<!-- auto-skills-start -->
comet-open: /root/.agents/skills/comet-open/SKILL.md | memory/skill_exp_comet-open.md
comet-design: /root/.agents/skills/comet-design/SKILL.md
comet-build: /root/.agents/skills/comet-build/SKILL.md
<!-- auto-skills-end -->
```

- 每行：`<name>: <SKILL.md_abs_path> [| <experience_file_path>]`
- 经验文件路径仅在文件存在时追加
- marker 之间由 `sync_skills_to_l1()` 维护
- marker 之外由 Agent 手动维护（备注、使用提示等）

### 3.4 Skill 发现逻辑（不变）

复用现有 `_scan_dir` + `_parse_skill_frontmatter`：
- 扫描 `$HOME/.agents/skills/` → 填充 dict
- 扫描 `$CWD/.agents/skills/` → 覆盖同名条目（项目级优先）
- frontmatter 解析：读前 2048 字节，正则提取 name + description
- name 为空 → 静默跳过

## 4. 关键决策

| 决策 | 选择 | 理由 | 替代方案（否决） |
|---|---|---|---|
| Skill 在 L3 中的定位 | 独立子类 | 信任边界不同（外部作者 vs Agent 自进化） | 合并为 L3 同类别（混淆信任模型） |
| L1 Skills 段格式 | 结构化多行 + HTML marker | 与 L1 现有段风格一致；marker 保护手动备注 | 段落标题分离（多占行数）；行内标记（解析复杂） |
| get_skill_catalog() 去留 | 完全移除 | 双通道混淆知识来源 | 保留精简版（一致性维护负担） |
| 自进化策略 | 经验文件为主 + 版本备份 opt-in | 经验文件在 Agent 领地，符合 L0；opt-in 处理 Skill 错误 | 纯经验文件（无法修 Skill 错误）；纯版本备份（外部更新冲突） |
| L1 更新机制 | 自动同步 + Agent 可微调 | 新 Skill 自动发现；Agent 可加使用备注 | 纯自动（Agent 无法微调）；纯手动（新 Skill 需手动发现） |
| do_start_long_term_update | 不改动 | L1 已含经验文件指针，Agent 自然覆盖 | 显式提及 Skill（过度耦合） |
| sync 时机 | 集成到 get_global_memory() | 覆盖每任务启动 + 每 10 轮重注入 | 独立定时器（额外复杂度） |

## 5. 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| L1 marker 语法引入 | 低 | HTML 注释对 Agent 语义透明；L1 是 Agent 可读文本 |
| L1 膨胀（>50 skills） | 中 | 每行极简（name + 路径）；两层映射策略（高频全路径，低频仅 name） |
| sync 覆盖手动备注 | 中 | marker 机制：sync 只更新 marker 之间，marker 外保留 |
| sync 性能 | 低 | Skill 数量少（<20），扫描毫秒级 |
| 经验文件命名冲突 | 低 | `skill_exp_` 前缀 + kebab-case name |
| 迁移：现有 catalog 消失 | 低 | 首次 sync 自动将现有 Skill 写入 L1 |
| opt-in 版本备份误操作 | 中 | 环境变量门禁 + 需用户确认 |

## 6. 测试策略

### 6.1 单元测试

| 测试 | 验证点 |
|---|---|
| `sync_skills_to_l1()` 基本功能 | 扫描 Skill → 正确格式化 L1 `[Skills]` 段 |
| 项目级覆盖用户级 | 同名 Skill 时 L1 条目指向项目级路径 |
| marker 机制 | sync 后 marker 之间内容更新，marker 外保留 |
| 经验文件检测 | 存在 `skill_exp_*.md` 时条目含双路径，不存在时仅含 SKILL.md 路径 |
| 空 Skill 目录 | 无 Skill 时 marker 之间为空，不报错 |
| frontmatter 解析 | 有/无 frontmatter、name 为空、目录名 ≠ frontmatter name |

### 6.2 集成测试

| 测试 | 验证点 |
|---|---|
| `get_system_prompt()` 无 catalog | 输出中无 `## Available Skills` 段 |
| L1 路由生效 | system_prompt 中 L1 `[Skills]` 段含 Skill 指针 |
| 热加载 | 新增 SKILL.md 后下次 `get_global_memory()` 调用时 L1 更新 |
| Skill 移除 | 删除 SKILL.md 后 L1 auto 段对应条目消失 |

### 6.3 Token 预算验证

| 测试 | 验证点 |
|---|---|
| L1 Skills 段行数 | ≤ Skill 数量（每 Skill 一行），不随 description 长度膨胀 |
| 17 skills 场景 | L1 Skills 段 ≈ 19 行（17 entries + 2 marker），远低于旧 catalog 的 500-800 tokens |

## 7. 迁移计划

1. 重写 `skill_loader.py`：`get_skill_catalog()` → `sync_skills_to_l1()` + `_replace_between_markers()`
2. 移除 `agentmain.py:46` 的 `get_skill_catalog()` 调用
3. 在 `ga.py:583` `get_global_memory()` 中添加 `sync_skills_to_l1()` 调用
4. 首次运行时 `sync_skills_to_l1()` 自动将现有 Skill 写入 L1 `[Skills]` 段
5. Agent 后续通过自进化自然积累 `memory/skill_exp_*.md`

**回滚策略**：
- 恢复 `agentmain.py:46` 的 `get_skill_catalog()` 调用
- 恢复旧 `skill_loader.py`
- L1 中的 marker 段可手动删除（不影响 L1 其他内容）

## 8. 边界条件

- **L1 文件不存在**：`sync_skills_to_l1()` 创建文件并写入 marker 段
- **L1 无 marker**：追加 marker 段到文件末尾
- **L1 marker 不配对**（有 start 无 end）：从 start 到文件末尾替换
- **Skill 目录不存在**：静默跳过，不报错
- **经验文件被删除**：下次 sync 时条目自动从双路径变为单路径
- **Skill 被外部更新**（git pull）：下次 sync 时路径不变，L1 条目不变（路径未变）；如 Skill name 变了，L1 条目 name 更新
