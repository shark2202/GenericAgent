---
change: integrate-skills-with-memory
design-doc: docs/superpowers/specs/2026-07-08-integrate-skills-with-memory-design.md
base-ref: 19eebd5d2ea211e231eb9a052ef314d6f9b2c6fb
---

# Plan: Skill 与 L1-L4 记忆体系集成

## 实施概览

将 Skill 从 system_prompt 末尾的全量 catalog 注入，改为通过 L1 索引层（`global_mem_insight.txt`）路由。核心改动 3 个文件：

| 文件 | 改动 | 规模 |
|---|---|---|
| `skill_loader.py` | 重写 `get_skill_catalog()` → `sync_skills_to_l1()` + 新增 `_replace_between_markers()` / `_discover_skills()` | 大（核心重写） |
| `agentmain.py` | 移除 `get_skill_catalog()` 调用与 import | 小（删 2 行） |
| `ga.py` | 在 `get_global_memory()` 中添加 `sync_skills_to_l1()` 调用 | 小（加 2-3 行） |

外加：新建测试文件、更新被破坏的 MCP 测试、可选增强函数。

## 前置条件

1. **基线确认**：当前分支基于 `19eebd5d`，工作区干净。
2. **ga.py 合并冲突**：`ga.py` 存在未解决的 git 合并冲突标记（`<<<<<<<` / `=======` / `>>>>>>>`）。实施前必须先解决冲突，确保 `get_global_memory()`（约 line 600-611）处于可用状态。本计划假设冲突已解决。
3. **L1 文件状态**：当前 `memory/global_mem_insight.txt` 不存在。`agentmain.py` line 36-39 有首次创建逻辑（从 template 复制）。`sync_skills_to_l1()` 的 `_replace_between_markers()` 已处理 `FileNotFoundError`，但首次运行应先让 `agentmain.py` 的初始化逻辑创建 L1 文件，或由 sync 函数自行创建空文件。
4. **Skill 目录现状**：用户级 `$HOME/.agents/skills/` 有 66 个 Skill；项目级 `.agents/skills/` 不存在。`_scan_dir` 已处理目录不存在的情况（静默返回空 dict）。

---

## 任务分解

### Phase 1: skill_loader.py 核心重写

> 对应 tasks.md §1（1.1-1.5）、§3（3.1-3.3）

**文件**：`/mnt/d/GenericAgent/skill_loader.py`

#### Step 1.0 — 添加模块级 `script_dir`

当前 `skill_loader.py` 无 `script_dir` 变量，但 `sync_skills_to_l1()` 需要用它定位 `memory/` 目录。在文件顶部 import 之后添加：

```python
import os
import re

SKILL_FILE = "SKILL.md"
# GA 安装目录（与 agentmain.py / ga.py 同级）
script_dir = os.path.dirname(os.path.abspath(__file__))
```

> **注意**：`script_dir` 必须用 `__file__` 计算，不能用 `os.getcwd()`。L1 文件位于 GA 安装目录的 `memory/` 下，而 Skill 发现仍用 `os.getcwd()` 扫描项目级目录。两者是不同概念。

#### Step 1.1 — 保留不变的两个函数

以下函数 **不改动**，原样保留：

- `_parse_skill_frontmatter(skill_md_path)` — frontmatter 解析（line 8-33）
- `_scan_dir(skills_root)` — 单目录扫描（line 36-51）

#### Step 1.2 — 新增 `_discover_skills()` 辅助函数

从旧 `get_skill_catalog()` 的函数体中提取 Skill 发现逻辑（用户级 + 项目级覆盖），独立为可复用函数：

```python
def _discover_skills(cwd_skills_root=None):
    """发现所有 Skill：用户级 + 项目级（项目级覆盖同名）。

    Args:
        cwd_skills_root: 覆盖 CWD 用于测试。默认 os.getcwd()。

    Returns:
        {name: (description, abs_skill_md_path)} dict，可能为空。
    """
    home = os.environ.get('HOME', '')
    user_root = os.path.join(home, '.agents', 'skills') if home else None
    cwd = cwd_skills_root or os.getcwd()
    project_root = os.path.join(cwd, '.agents', 'skills')

    catalog = {}
    if user_root:
        catalog.update(_scan_dir(user_root))
    catalog.update(_scan_dir(project_root))  # 项目级覆盖用户级
    return catalog
```

> 对应 tasks.md 1.4（保留发现核心逻辑）。此逻辑与旧 `get_skill_catalog()` body 完全一致，只是抽成独立函数。

#### Step 1.3 — 新增 `_replace_between_markers()` 函数

marker 段替换的核心机制。严格按设计文档 §3.2.1 实现：

```python
def _replace_between_markers(filepath, start_marker, end_marker, new_content):
    """替换文件中 start_marker ~ end_marker 之间的内容，保留外部。

    边界条件：
    - 文件不存在 → 创建空文件后写入 new_content
    - marker 不存在 → 追加到文件末尾
    - 仅有 start 无 end → 从 start 替换到文件末尾
    """
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
            # 有 start 无 end → 替换到文件末尾
            end_idx = len(content)
        else:
            end_idx += len(end_marker)
        content = content[:start_idx] + new_content + content[end_idx:]

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
```

> 对应 tasks.md 1.2（marker 机制）、设计文档 §3.2.1、§8（边界条件）。

#### Step 1.4 — 重写 `get_skill_catalog()` → `sync_skills_to_l1()`

**删除** 旧 `get_skill_catalog()`（line 54-85，含 catalog 格式化逻辑）。

**新增** `sync_skills_to_l1()`：

```python
# 常量定义
SKILL_START_MARKER = '<!-- auto-skills-start -->'
SKILL_END_MARKER = '<!-- auto-skills-end -->'


def sync_skills_to_l1(l1_path=None):
    """扫描 Skill 目录 → 同步到 L1 [Skills] 段（auto marker 之间）。

    保留 marker 外的手动备注。每行格式：
        <name>: <SKILL.md_abs_path>
        <name>: <SKILL.md_abs_path> | <experience_file_path>  (经验文件存在时)

    Args:
        l1_path: L1 文件路径覆盖（用于测试）。默认 <script_dir>/memory/global_mem_insight.txt
    """
    l1_path = l1_path or os.path.join(script_dir, 'memory', 'global_mem_insight.txt')

    # 1. 扫描 Skill（复用发现逻辑）
    catalog = _discover_skills()  # {name: (desc, skill_md_path)}

    # 2. 检测经验文件，构建条目
    entries = []
    for name, (desc, skill_path) in sorted(catalog.items()):
        exp_path = os.path.join(script_dir, 'memory', f'skill_exp_{name}.md')
        if os.path.isfile(exp_path):
            entries.append(f"{name}: {skill_path} | {exp_path}")
        else:
            entries.append(f"{name}: {skill_path}")

    # 3. 构建 auto 段内容
    auto_block = f"{SKILL_START_MARKER}\n"
    if entries:
        auto_block += "\n".join(entries) + "\n"
    auto_block += SKILL_END_MARKER

    # 4. 替换 marker 之间内容，保留外部
    _replace_between_markers(l1_path, SKILL_START_MARKER, SKILL_END_MARKER, auto_block)
```

> 对应 tasks.md 1.1（重写）、1.3（经验文件检测）、1.5（移除 catalog 格式化）、3.1（L1 段格式）、3.2（marker 语法）、3.3（首次迁移）。

#### Step 1.5 — 移除 catalog 格式化逻辑确认

确认旧代码已完全删除：
- ~~`lines = ["## Available Skills", ""]`~~
- ~~`lines.append(f"- **{name}**: {desc} ({path})")`~~
- ~~`return "\n".join(lines) + "\n"`~~

`get_skill_catalog()` 函数签名与实现全部移除，不保留兼容包装。

#### Phase 1 完成后 skill_loader.py 结构

```
skill_loader.py
├── import os, re
├── SKILL_FILE = "SKILL.md"
├── script_dir = ...                          # [新增] Step 1.0
├── SKILL_START_MARKER / SKILL_END_MARKER     # [新增] Step 1.4
├── _parse_skill_frontmatter()                # [保留] 不变
├── _scan_dir()                               # [保留] 不变
├── _discover_skills()                        # [新增] Step 1.2
├── _replace_between_markers()                # [新增] Step 1.3
└── sync_skills_to_l1()                       # [新增→替换] Step 1.4, 替换 get_skill_catalog()
```

---

### Phase 2: agentmain.py 改动

> 对应 tasks.md §2（2.1-2.3）

**文件**：`/mnt/d/GenericAgent/agentmain.py`

#### Step 2.1 — 更新 import

**Line 12**，将：
```python
from skill_loader import get_skill_catalog
```
改为：
```python
from skill_loader import sync_skills_to_l1
```

> 如果 `get_skill_catalog` 在 agentmain.py 中无其他引用（已确认仅在 line 50 调用），可直接替换。如果后续不再直接调用 `sync_skills_to_l1`（因为集成在 `get_global_memory()` 中），可考虑移除此 import。但保留以备 `get_system_prompt()` 中可能的直接调用或测试注入。**推荐**：移除此 import，因为 sync 逻辑已移入 `get_global_memory()`，agentmain.py 不再需要直接引用 skill_loader。

最终 line 12 **删除**（不再 import skill_loader）。

#### Step 2.2 — 修改 `get_system_prompt()`

**Lines 47-52**，当前：
```python
def get_system_prompt():
    with open(os.path.join(script_dir, f'assets/sys_prompt{lang_suffix}.txt'), 'r', encoding='utf-8') as f: prompt = f.read()
    prompt += f"\nToday: {time.strftime('%Y-%m-%d %a')}\n"
    prompt += get_global_memory()
    prompt += get_skill_catalog()   # ← 删除此行
    return prompt
```

改为：
```python
def get_system_prompt():
    with open(os.path.join(script_dir, f'assets/sys_prompt{lang_suffix}.txt'), 'r', encoding='utf-8') as f: prompt = f.read()
    prompt += f"\nToday: {time.strftime('%Y-%m-%d %a')}\n"
    prompt += get_global_memory()
    return prompt
```

> 对应 tasks.md 2.1。删除 `prompt += get_skill_catalog()` 这一行。

#### Step 2.3 — 确认 L1 Skills 段通过 `get_global_memory()` 注入

`get_global_memory()`（ga.py line 600-611）读取 `memory/global_mem_insight.txt` 全文注入 system_prompt。sync 后 L1 文件含 marker 段，marker 段含 Skill 条目，因此 **无需额外改动** 即可通过现有注入机制进入 system_prompt。

> 对应 tasks.md 2.2、2.3。此步骤为验证步骤，在 Phase 8 测试中确认。

---

### Phase 3: ga.py 集成 sync 调用

> 对应设计文档 §3.2.3

**文件**：`/mnt/d/GenericAgent/ga.py`

#### Step 3.1 — 添加 import

在 ga.py 顶部 import 区（`script_dir` 定义附近，约 line 10-16），添加：

```python
from skill_loader import sync_skills_to_l1
```

> **放置位置**：ga.py 的 import 区。注意 ga.py 存在合并冲突，需在解决冲突后的最终版本中添加。

#### Step 3.2 — 在 `get_global_memory()` 中添加 sync 调用

**Lines 600-611**，当前：
```python
def get_global_memory():
    prompt = "\n"
    try:
        suffix = '_en' if os.environ.get('GA_LANG', '') == 'en' else ''
        with open(os.path.join(script_dir, 'memory/global_mem_insight.txt'), 'r', encoding='utf-8', errors='replace') as f: insight = f.read()
        with open(os.path.join(script_dir, f'assets/insight_fixed_structure{suffix}.txt'), 'r', encoding='utf-8') as f: structure = f.read()
        prompt += f'cwd = {os.path.join(script_dir, "temp")} (./)\n'
        prompt += f"\n[Memory] (../memory)\n"
        prompt += structure + '\n../memory/global_mem_insight.txt:\n'
        prompt += insight + "\n"
    except FileNotFoundError: pass
    return prompt
```

改为（在 `try` 块内、读取 L1 文件之前添加 sync 调用）：
```python
def get_global_memory():
    prompt = "\n"
    try:
        # 同步 Skill 到 L1（marker 段），再读取 L1 全文注入 prompt
        try:
            sync_skills_to_l1()
        except Exception:
            pass  # sync 失败不应阻断记忆注入
        suffix = '_en' if os.environ.get('GA_LANG', '') == 'en' else ''
        with open(os.path.join(script_dir, 'memory/global_mem_insight.txt'), 'r', encoding='utf-8', errors='replace') as f: insight = f.read()
        with open(os.path.join(script_dir, f'assets/insight_fixed_structure{suffix}.txt'), 'r', encoding='utf-8') as f: structure = f.read()
        prompt += f'cwd = {os.path.join(script_dir, "temp")} (./)\n'
        prompt += f"\n[Memory] (../memory)\n"
        prompt += structure + '\n../memory/global_mem_insight.txt:\n'
        prompt += insight + "\n"
    except FileNotFoundError: pass
    return prompt
```

> **关键设计决策**：
> - sync 调用放在 `try` 块内、读取 L1 **之前**，确保读到的 L1 已含最新 Skill 条目。
> - sync 自身用独立 `try/except Exception` 包裹，防止 sync 异常（如写权限问题）阻断整个记忆注入。设计文档未提及此防护，但生产环境必需。
> - sync 时机：`get_global_memory()` 在每任务启动（`get_system_prompt()`）和每 10 轮（`turn_end_callback` line 587）调用，覆盖热加载需求。

#### Step 3.3 — 确认 `turn_end_callback` 中的调用路径

ga.py line 587（约）：
```python
elif turn % 10 == 0: next_prompt += get_global_memory()
```

此行调用 `get_global_memory()`，间接触发 sync。**无需改动**，sync 自动随每 10 轮重新注入时执行。

---

### Phase 4: L1 索引格式定义

> 对应 tasks.md §3（3.1-3.3），已在 Phase 1 实现中完成

#### Step 4.1 — 格式定义（文档性，无代码改动）

L1 `[Skills]` 段最终格式（`global_mem_insight.txt` 内）：

```
<!-- auto-skills-start -->
comet-open: /root/.agents/skills/comet-open/SKILL.md | memory/skill_exp_comet-open.md
comet-design: /root/.agents/skills/comet-design/SKILL.md
comet-build: /root/.agents/skills/comet-build/SKILL.md
<!-- auto-skills-end -->
# Agent 手动备注区（不受 sync 影响）
```

- 每行：`<name>: <SKILL.md_abs_path> [| <experience_file_path>]`
- 经验文件路径仅在 `memory/skill_exp_<name>.md` 存在时追加
- marker 之间由 `sync_skills_to_l1()` 维护
- marker 之外由 Agent 手动维护

#### Step 4.2 — 首次运行迁移

首次调用 `sync_skills_to_l1()` 时：
1. L1 文件不存在 → `_replace_between_markers` 创建空内容 → 追加 marker 段
2. L1 文件存在但无 marker → 追加 marker 段到末尾
3. 现有 Skill 自动写入 marker 段

**无需手动迁移脚本**，sync 函数自动处理。

---

### Phase 5: 工作记忆集成（验证，无代码改动）

> 对应 tasks.md §4（4.1-4.2）、设计文档 §3.2.4

#### Step 5.1 — 确认 `related_sop` 机制

`related_sop` 是 Agent 自主写入的工作记忆字段（通过 `do_update_working_checkpoint`）。Agent 使用 Skill 时通过 `file_read` 读取 SKILL.md，路径自然出现在 `related_sop` 中。

**验证点**：
- [ ] 确认 `do_update_working_checkpoint` 接受 `related_sop` 字段且无硬编码过滤
- [ ] 确认 Agent `file_read` SKILL.md 后路径可写入 `related_sop`

**结论**：无需改动 ga.py 工作记忆相关代码。

#### Step 5.2 — 验证跨任务继承

agentmain.py line 180-184（约）已有跨任务继承机制：
```python
if self.handler and 'key_info' in self.handler.working: 
    ki = re.sub(...)
    handler.working['key_info'] = ki
    handler.working['passed_sessions'] = ...
```

**验证点**：
- [ ] 新 handler 继承旧 handler 的 `key_info`（含 Skill 使用上下文）
- [ ] `related_sop` 是否也在继承范围内（检查 `working` dict 的传递逻辑）

**结论**：无需改动，复用现有机制。

---

### Phase 6: 自进化经验文件（验证，无代码改动）

> 对应 tasks.md §5（5.1-5.3）、设计文档 §3.2.5

#### Step 6.1 — 确认 `do_start_long_term_update` 无需改动

`do_start_long_term_update`（ga.py，约 line 508）返回 L0 规则 + 提取 prompt，指导 Agent 更新 L1/L2/L3。Skill 经验文件属于 L3。

**验证点**：
- [ ] L1 `[Skills]` 段已含经验文件路径指针
- [ ] Agent 通过 L1 自然发现 `memory/skill_exp_<name>.md` 路径
- [ ] Agent 通过标准 `file_patch` 写入经验文件

**结论**：无需修改 `do_start_long_term_update`。

#### Step 6.2 — 验证 Agent 能写入经验文件

**验证点**：
- [ ] Agent 决策流：L1 看到 `skill_exp_comet-open.md` 路径 → `file_read` 现有经验 → `file_patch` 追加新经验
- [ ] 下次 `sync_skills_to_l1()` 自动检测新经验文件 → L1 条目从单路径变双路径

#### Step 6.3 — 验证 L1 路由双指向

**验证点**：
- [ ] L1 同一行同时指向 SKILL.md（技能本体）和 `skill_exp_*.md`（使用经验）
- [ ] Agent 可按需 `file_read` 两者

---

### Phase 7: 可选增强 — 版本备份

> 对应 tasks.md §6（6.1-6.2）、设计文档 §3.2.6

**优先级**：可选（opt-in），可在核心功能完成后实施。

#### Step 7.1 — 实现 `backup_and_patch_skill()`

**文件**：`/mnt/d/GenericAgent/skill_loader.py`

在文件顶部 import 区添加：
```python
import shutil
```

在 `sync_skills_to_l1()` 之后添加：
```python
def backup_and_patch_skill(skill_md_path, patch_content):
    """opt-in: 备份 SKILL.md 原版后交由 Agent patch。

    需 GA_SKILL_PATCH_ENABLED=1 环境变量。
    此函数仅负责备份，实际 patch 由 Agent 的 file_patch 工具执行。

    Args:
        skill_md_path: SKILL.md 绝对路径
        patch_content: 预期 patch 内容（当前未使用，保留参数用于未来校验）

    Raises:
        PermissionError: 环境变量未设置
    """
    if os.environ.get('GA_SKILL_PATCH_ENABLED') != '1':
        raise PermissionError("Skill patch requires GA_SKILL_PATCH_ENABLED=1")

    bak_path = skill_md_path + '.bak'
    if not os.path.exists(bak_path):
        shutil.copy2(skill_md_path, bak_path)
    # Agent 通过 file_patch 修改 SKILL.md，此函数仅负责备份
```

#### Step 7.2 — 用户确认门禁

- 环境变量 `GA_SKILL_PATCH_ENABLED=1` 为门禁（默认关闭）
- **验证点**：
  - [ ] 未设置环境变量时调用 → 抛 `PermissionError`
  - [ ] 设置后调用 → 创建 `.bak` 文件（仅首次，不覆盖已有备份）
  - [ ] Agent 需先调用此函数备份，再用 `file_patch` 修改 SKILL.md

---

### Phase 8: 测试

> 对应 tasks.md §7（7.1-7.7）、设计文档 §6

#### Step 8.1 — 更新被破坏的现有测试

**文件**：`/mnt/d/GenericAgent/tests/test_mcp_memory_integration.py`

当前有 3 个测试直接调用 `get_skill_catalog()`（line 25-68）：
- `test_skill_catalog_includes_mcp_tools`
- `test_skill_catalog_without_mcp`
- `test_skill_catalog_mcp_isolation`

**处理方式**：`get_skill_catalog()` 已移除，这 3 个测试会 `ImportError`。选择方案：

- **方案 A（推荐）**：删除这 3 个测试函数。MCP catalog 注入功能随 `get_skill_catalog()` 一起移除，MCP 工具应通过其他路径（如 L1 或 MCP SOP）暴露给 Agent。
- **方案 B**：如果 MCP catalog 注入仍需保留，将其迁移到 `sync_skills_to_l1()` 中追加 MCP 段。但这超出本 change 范围，不建议。

保留 `test_mcp_memory_integration.py` 中的其他 4 个测试（`test_mcp_sop_exists`、`test_mcp_sop_content`、`test_map_mentions_mcp`、`test_map_mentions_mcp_files`），它们不依赖 `get_skill_catalog()`。

#### Step 8.2 — 新建单元测试文件

**文件**：`/mnt/d/GenericAgent/tests/test_skill_loader_l1.py`

参照现有测试结构（`test_mcp_memory_integration.py` 的 `_REPO_ROOT` + `sys.path` 模式），使用 `pytest` + `unittest.mock` + `tmp_path` fixture。

```python
"""
Tests for skill_loader L1 integration.

Verifies sync_skills_to_l1() correctly:
  - Scans skills and formats L1 [Skills] section
  - Project-level overrides user-level
  - Marker mechanism preserves manual notes
  - Experience file detection (dual path vs single path)
  - Edge cases: empty skills, no L1 file, unpaired markers, etc.
"""
import os
import sys
from unittest.mock import patch

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import skill_loader
from skill_loader import (
    sync_skills_to_l1,
    _replace_between_markers,
    _discover_skills,
    SKILL_START_MARKER,
    SKILL_END_MARKER,
)
```

#### Step 8.3 — 单元测试用例

以下为每个测试的名称、验证点与核心结构：

**8.3.1 `test_sync_basic`** — tasks.md 7.1, 设计文档 6.1
```python
def test_sync_basic(tmp_path):
    """sync_skills_to_l1 正确扫描并格式化 L1 Skills 段。"""
    # 创建临时 skill 目录结构
    skills_dir = tmp_path / ".agents" / "skills" / "my-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        '---\nname: my-skill\ndescription: "Test skill"\n---\n# Body\n'
    )
    l1_path = str(tmp_path / "l1.txt")

    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"my-skill": ("Test skill", str(skills_dir / "SKILL.md"))}
        sync_skills_to_l1(l1_path=l1_path)

    content = open(l1_path).read()
    assert SKILL_START_MARKER in content
    assert SKILL_END_MARKER in content
    assert "my-skill:" in content
    assert "SKILL.md" in content
```

**8.3.2 `test_project_overrides_user`** — tasks.md 7.2, 设计文档 6.1
```python
def test_project_overrides_user(tmp_path):
    """项目级 Skill 覆盖用户级同名 Skill。"""
    user_skills = tmp_path / "user" / ".agents" / "skills" / "shared"
    user_skills.mkdir(parents=True)
    (user_skills / "SKILL.md").write_text('---\nname: shared\ndescription: "User"\n---\n')

    proj_skills = tmp_path / "proj" / ".agents" / "skills" / "shared"
    proj_skills.mkdir(parents=True)
    (proj_skills / "SKILL.md").write_text('---\nname: shared\ndescription: "Project"\n---\n')

    with patch.dict(os.environ, {"HOME": str(tmp_path / "user")}):
        catalog = _discover_skills(cwd_skills_root=str(tmp_path / "proj"))

    assert "shared" in catalog
    assert catalog["shared"][0] == "Project"  # 项目级 description
    assert str(tmp_path / "proj") in catalog["shared"][1]  # 项目级路径
```

**8.3.3 `test_marker_preserves_manual_notes`** — tasks.md 7.3, 设计文档 6.1
```python
def test_marker_preserves_manual_notes(tmp_path):
    """sync 后 marker 之间内容更新，marker 外手动备注保留。"""
    l1_path = str(tmp_path / "l1.txt")
    # 预置 L1：有手动备注 + 旧 marker 段
    pre_content = "# Manual notes before\n"
    pre_content += f"{SKILL_START_MARKER}\nold-skill: /old/path/SKILL.md\n{SKILL_END_MARKER}\n"
    pre_content += "# Manual notes after\n"
    open(l1_path, 'w').write(pre_content)

    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"new-skill": ("Desc", "/new/path/SKILL.md")}
        sync_skills_to_l1(l1_path=l1_path)

    content = open(l1_path).read()
    assert "# Manual notes before" in content
    assert "# Manual notes after" in content
    assert "old-skill" not in content      # 旧条目被替换
    assert "new-skill" in content          # 新条目已写入
```

**8.3.4 `test_experience_file_dual_path`** — tasks.md 7.4, 设计文档 6.1
```python
def test_experience_file_dual_path(tmp_path):
    """经验文件存在时 L1 条目含双路径，不存在时仅含 SKILL.md 路径。"""
    l1_path = str(tmp_path / "l1.txt")
    skill_path = str(tmp_path / "skill" / "SKILL.md")
    os.makedirs(os.path.dirname(skill_path), exist_ok=True)
    open(skill_path, 'w').write('---\nname: test\ndescription: "T"\n---\n')

    # 场景 1：无经验文件 → 单路径
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"test": ("T", skill_path)}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    assert "test: " + skill_path in content
    assert "|" not in content.split("test:")[1].split("\n")[0]

    # 场景 2：创建经验文件 → 双路径
    exp_path = os.path.join(skill_loader.script_dir, 'memory', 'skill_exp_test.md')
    os.makedirs(os.path.dirname(exp_path), exist_ok=True)
    open(exp_path, 'w').write("# Experience")
    try:
        with patch.object(skill_loader, '_discover_skills') as mock_disc:
            mock_disc.return_value = {"test": ("T", skill_path)}
            sync_skills_to_l1(l1_path=l1_path)
        content = open(l1_path).read()
        assert "|" in content
        assert "skill_exp_test.md" in content
    finally:
        os.remove(exp_path)  # 清理
```

**8.3.5 `test_empty_skills`** — 设计文档 6.1
```python
def test_empty_skills(tmp_path):
    """无 Skill 时 marker 之间为空，不报错。"""
    l1_path = str(tmp_path / "l1.txt")
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    assert SKILL_START_MARKER in content
    assert SKILL_END_MARKER in content
    # marker 之间无条目行
    auto_section = content[content.index(SKILL_START_MARKER):content.index(SKILL_END_MARKER)]
    lines = [l for l in auto_section.split('\n') if l and not l.startswith('<!--')]
    assert len(lines) == 0
```

**8.3.6 `test_frontmatter_edge_cases`** — 设计文档 6.1
```python
def test_frontmatter_no_name(tmp_path):
    """frontmatter 无 name → 静默跳过。"""
    skills_dir = tmp_path / ".agents" / "skills" / "no-name"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text('---\ndescription: "No name"\n---\n')
    catalog = _discover_skills(cwd_skills_root=str(tmp_path))
    assert len(catalog) == 0  # 无 name 的被跳过

def test_frontmatter_dirname_vs_name(tmp_path):
    """目录名 ≠ frontmatter name → 用 frontmatter name 作 key。"""
    skills_dir = tmp_path / ".agents" / "skills" / "dir-name"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text('---\nname: real-name\ndescription: "X"\n---\n')
    catalog = _discover_skills(cwd_skills_root=str(tmp_path))
    assert "real-name" in catalog
    assert "dir-name" not in catalog

def test_no_frontmatter(tmp_path):
    """无 frontmatter → 静默跳过。"""
    skills_dir = tmp_path / ".agents" / "skills" / "plain"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text('# Just markdown, no frontmatter\n')
    catalog = _discover_skills(cwd_skills_root=str(tmp_path))
    assert len(catalog) == 0
```

**8.3.7 `test_l1_file_not_exist`** — 设计文档 §8
```python
def test_l1_file_not_exist(tmp_path):
    """L1 文件不存在 → 创建文件并写入 marker 段。"""
    l1_path = str(tmp_path / "nonexistent" / "l1.txt")
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"s": ("D", "/p/SKILL.md")}
        # 目录不存在
        sync_skills_to_l1(l1_path=l1_path)
    assert os.path.isfile(l1_path)
    content = open(l1_path).read()
    assert SKILL_START_MARKER in content
```

**8.3.8 `test_no_marker_append`** — 设计文档 §8
```python
def test_no_marker_append(tmp_path):
    """L1 无 marker → 追加 marker 段到末尾。"""
    l1_path = str(tmp_path / "l1.txt")
    open(l1_path, 'w').write("# Existing content\nNo markers here.")
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"s": ("D", "/p/SKILL.md")}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    assert "# Existing content" in content
    assert content.index("# Existing content") < content.index(SKILL_START_MARKER)
```

**8.3.9 `test_unpaired_marker`** — 设计文档 §8
```python
def test_unpaired_marker(tmp_path):
    """有 start 无 end → 从 start 替换到文件末尾。"""
    l1_path = str(tmp_path / "l1.txt")
    open(l1_path, 'w').write(f"before\n{SKILL_START_MARKER}\norphan content\n")
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"s": ("D", "/p/SKILL.md")}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    assert "before" in content
    assert "orphan content" not in content
    assert SKILL_END_MARKER in content
```

**8.3.10 `test_exp_deleted_reverts`** — 设计文档 §8
```python
def test_exp_deleted_reverts(tmp_path):
    """经验文件被删除 → 下次 sync 条目从双路径变单路径。"""
    l1_path = str(tmp_path / "l1.txt")
    skill_path = "/p/SKILL.md"
    exp_path = os.path.join(skill_loader.script_dir, 'memory', 'skill_exp_revert.md')
    os.makedirs(os.path.dirname(exp_path), exist_ok=True)

    # 第一次 sync：有经验文件 → 双路径
    open(exp_path, 'w').write("# Exp")
    with patch.object(skill_loader, '_discover_skills') as m:
        m.return_value = {"revert": ("D", skill_path)}
        sync_skills_to_l1(l1_path=l1_path)
    assert "|" in open(l1_path).read()

    # 删除经验文件，第二次 sync → 单路径
    os.remove(exp_path)
    with patch.object(skill_loader, '_discover_skills') as m:
        m.return_value = {"revert": ("D", skill_path)}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    line = [l for l in content.split('\n') if l.startswith('revert:')][0]
    assert "|" not in line
```

#### Step 8.4 — 集成测试

**8.4.1 `test_no_catalog_in_system_prompt`** — tasks.md 7.5, 设计文档 6.2
```python
def test_no_catalog_in_system_prompt(monkeypatch, tmp_path):
    """get_system_prompt() 输出中无 '## Available Skills' 段。"""
    # mock sync_skills_to_l1 避免真实文件操作
    monkeypatch.setattr('ga.sync_skills_to_l1', lambda *a, **k: None)
    monkeypatch.setattr('agentmain.get_global_memory', lambda: "[Memory]\nL1 content\n")
    from agentmain import get_system_prompt
    prompt = get_system_prompt()
    assert "## Available Skills" not in prompt
```

**8.4.2 `test_l1_skills_in_prompt`** — 设计文档 6.2
```python
def test_l1_skills_route_via_l1(tmp_path, monkeypatch):
    """system_prompt 中 L1 段含 Skill 指针（通过 get_global_memory 注入）。"""
    # 准备 L1 文件含 marker 段
    l1 = tmp_path / "global_mem_insight.txt"
    l1.write_text(f"{SKILL_START_MARKER}\ncomet-open: /path/SKILL.md\n{SKILL_END_MARKER}\n")
    monkeypatch.setattr('ga.script_dir', str(tmp_path))
    monkeypatch.setattr('ga.sync_skills_to_l1', lambda *a, **k: None)
    from ga import get_global_memory
    prompt = get_global_memory()
    assert "comet-open:" in prompt
    assert SKILL_START_MARKER in prompt
```

**8.4.3 `test_hot_reload`** — tasks.md 7.6, 设计文档 6.2
```python
def test_hot_reload(tmp_path, monkeypatch):
    """新增 SKILL.md 后下次 get_global_memory() 调用时 L1 更新。"""
    l1_path = str(tmp_path / "l1.txt")
    monkeypatch.setattr(skill_loader, 'script_dir', str(tmp_path))
    os.makedirs(os.path.join(str(tmp_path), 'memory'), exist_ok=True)
    monkeypatch.setattr(skill_loader, 'sync_skills_to_l1', 
                        lambda l1_path=None: sync_skills_to_l1(
                            l1_path=l1_path or os.path.join(str(tmp_path), 'memory', 'global_mem_insight.txt')))

    # 初始：无 skill
    sync_skills_to_l1(l1_path=l1_path)
    assert "new-skill" not in open(l1_path).read()

    # 新增 skill
    skills_dir = tmp_path / ".agents" / "skills" / "new-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text('---\nname: new-skill\ndescription: "New"\n---\n')
    monkeypatch.setenv("HOME", str(tmp_path))

    # 再次 sync
    sync_skills_to_l1(l1_path=l1_path)
    assert "new-skill:" in open(l1_path).read()
```

**8.4.4 `test_skill_removal`** — 设计文档 6.2
```python
def test_skill_removal(tmp_path, monkeypatch):
    """删除 SKILL.md 后 L1 auto 段对应条目消失。"""
    l1_path = str(tmp_path / "l1.txt")
    skill_dir = tmp_path / ".agents" / "skills" / "temp-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text('---\nname: temp-skill\ndescription: "T"\n---\n')
    monkeypatch.setenv("HOME", str(tmp_path))

    sync_skills_to_l1(l1_path=l1_path)
    assert "temp-skill:" in open(l1_path).read()

    # 删除 skill
    import shutil
    shutil.rmtree(skill_dir)
    sync_skills_to_l1(l1_path=l1_path)
    assert "temp-skill:" not in open(l1_path).read()
```

#### Step 8.5 — Token 预算验证

**8.5.1 `test_token_budget`** — tasks.md 7.7, 设计文档 6.3
```python
def test_token_budget(tmp_path, monkeypatch):
    """L1 Skills 段行数 ≤ Skill 数量 + 2（marker），不随 description 膨胀。"""
    l1_path = str(tmp_path / "l1.txt")
    # 模拟 17 个 skill，description 各 500 字
    catalog = {}
    for i in range(17):
        catalog[f"skill-{i:02d}"] = ("x" * 500, f"/path/skill-{i:02d}/SKILL.md")

    with patch.object(skill_loader, '_discover_skills', return_value=catalog):
        sync_skills_to_l1(l1_path=l1_path)

    content = open(l1_path).read()
    auto_section = content[content.index(SKILL_START_MARKER):content.index(SKILL_END_MARKER) + len(SKILL_END_MARKER)]
    line_count = len([l for l in auto_section.strip().split('\n') if l])
    # 17 entries + 2 markers = 19 行
    assert line_count == 19
    # 确认无 description 内容泄漏
    assert "x" * 100 not in content
```

#### Step 8.6 — 可选增强测试

```python
def test_backup_skill_disabled():
    """未设 GA_SKILL_PATCH_ENABLED → 抛 PermissionError。"""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(PermissionError):
            backup_and_patch_skill("/fake/SKILL.md", "patch")

def test_backup_skill_creates_bak(tmp_path):
    """设置环境变量后创建 .bak 文件（仅首次）。"""
    skill = tmp_path / "SKILL.md"
    skill.write_text("original")
    with patch.dict(os.environ, {"GA_SKILL_PATCH_ENABLED": "1"}):
        backup_and_patch_skill(str(skill), "patch")
        assert (tmp_path / "SKILL.md.bak").exists()
        assert (tmp_path / "SKILL.md.bak").read_text() == "original"
        # 二次调用不覆盖已有备份
        skill.write_text("modified")
        backup_and_patch_skill(str(skill), "patch")
        assert (tmp_path / "SKILL.md.bak").read_text() == "original"
```

#### Step 8.7 — 运行测试

```bash
# 全部新测试
python -m pytest tests/test_skill_loader_l1.py -v

# 确认 MCP 测试未被破坏（删除 get_skill_catalog 测试后）
python -m pytest tests/test_mcp_memory_integration.py -v

# 全量回归
python -m pytest tests/ -v
```

---

## 执行顺序

```
Phase 1 (skill_loader.py 重写)
  │
  ├── 1.0 添加 script_dir
  ├── 1.1 确认保留 _parse_skill_frontmatter / _scan_dir
  ├── 1.2 新增 _discover_skills()
  ├── 1.3 新增 _replace_between_markers()
  └── 1.4 重写 get_skill_catalog() → sync_skills_to_l1()
         │
         ▼
Phase 2 (agentmain.py)          Phase 3 (ga.py)
  ├── 2.1 删除 import             ├── 3.1 添加 import
  └── 2.2 删除 catalog 调用       └── 3.2 添加 sync 调用
         │                              │
         └──────────┬───────────────────┘
                    ▼
         Phase 4 (L1 格式 — 已在 Phase 1 实现)
                    │
                    ▼
         Phase 8.1-8.5 (测试 — 单元 + 集成 + token)
                    │
                    ▼
         Phase 5-6 (验证工作记忆 & 自进化 — 无代码改动)
                    │
                    ▼
         Phase 7 (可选：版本备份)
                    │
                    ▼
         Phase 8.6-8.7 (可选测试 + 全量回归)
```

**关键依赖**：
- Phase 1 必须先完成（其他 Phase 依赖 `sync_skills_to_l1()` 存在）
- Phase 2 和 Phase 3 可并行（互不依赖）
- Phase 8 测试依赖 Phase 1-3 全部完成
- Phase 5-6 是验证步骤，可在 Phase 8 集成测试中一并完成

---

## 回滚策略

若需回滚：

1. **恢复 `agentmain.py`**：重新添加 `from skill_loader import get_skill_catalog` import 和 `prompt += get_skill_catalog()` 调用
2. **恢复 `ga.py`**：移除 `from skill_loader import sync_skills_to_l1` import 和 `sync_skills_to_l1()` 调用
3. **恢复 `skill_loader.py`**：从 git 恢复旧版本（`git checkout HEAD -- skill_loader.py`）
4. **清理 L1**：手动删除 `global_mem_insight.txt` 中的 `<!-- auto-skills-start -->` ~ `<!-- auto-skills-end -->` 段（不影响 L1 其他内容）
5. **恢复测试**：`git checkout HEAD -- tests/test_mcp_memory_integration.py`

---

## 风险与注意事项

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| **ga.py 合并冲突未解决** | 高 | 实施前必须先 `git status` 检查并解决冲突，确保 `get_global_memory()` 可用 |
| **MCP 测试被破坏** | 中 | `test_mcp_memory_integration.py` 的 3 个 `get_skill_catalog()` 测试需删除（Step 8.1） |
| **skill_loader.py 无 `script_dir`** | 中 | Step 1.0 添加模块级 `script_dir = os.path.dirname(os.path.abspath(__file__))` |
| **sync 异常阻断记忆注入** | 中 | Step 3.2 用独立 `try/except Exception` 包裹 sync 调用 |
| **L1 文件不存在** | 低 | `_replace_between_markers` 已处理 `FileNotFoundError`；agentmain.py 初始化逻辑也会创建 |
| **经验文件命名冲突** | 低 | `skill_exp_` 前缀 + Skill name（kebab-case） |
| **首次运行 L1 无 marker** | 低 | `_replace_between_markers` 追加到末尾（Step 1.3 边界条件） |
| **66 个 Skill 注入 L1** | 低 | 每行极简（name + 路径），约 68 行（66 entries + 2 marker），远低于旧 catalog token 量 |

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `skill_loader.py` | 重写 | `get_skill_catalog()` → `sync_skills_to_l1()` + 新增 `_discover_skills()` / `_replace_between_markers()` / `backup_and_patch_skill()` + `script_dir` |
| `agentmain.py` | 修改 | 删除 line 12 import、line 50 catalog 调用（共 2 行） |
| `ga.py` | 修改 | 添加 import + `get_global_memory()` 中 sync 调用（约 4 行） |
| `tests/test_skill_loader_l1.py` | 新建 | 16+ 个单元/集成测试用例 |
| `tests/test_mcp_memory_integration.py` | 修改 | 删除 3 个 `get_skill_catalog()` 测试函数 |
