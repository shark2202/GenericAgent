---
comet_change: add-skill-support
role: technical-design
canonical_spec: openspec
---

# Design Doc: AGENT SKILL 加载支持

## 架构概览

```
$HOME/.agents/skills/ ─┐
                        ├──> skill_loader.py ──> get_skill_catalog()
$CWD/.agents/skills/  ─┘         │
                                  │ 返回格式化 catalog 字符串
                                  ▼
                         agentmain.py: get_system_prompt()
                                  │
                                  ▼
                         system_prompt (末尾追加)
                                  │
                                  ▼
                         agent 通过 file_read 按需加载 SKILL.md
```

## 模块设计

### skill_loader.py

暴露单一接口：`get_skill_catalog() -> str`

**内部流程**：

```
1. 初始化空 dict {name: (description, path)}
2. 扫描 $HOME/.agents/skills/ → 填充 dict
3. 扫描 $CWD/.agents/skills/   → 覆盖同名条目（项目级优先）
4. 格式化输出：
   - 无 skill → 返回 ""
   - 有 skill → 返回 "## Available Skills\n\n" + 逐行列表
```

**SKILL.md 解析**：
- 读文件前 20 行，检查是否以 `---` 开头
- 若匹配，提取 `---...---` 之间的 frontmatter 文本
- 用正则 `name:\s*(.+)` 提取 name（必需）
- 用正则 `description:\s*["']?(.+?)["']?\s*$` 提取 description（可选）
- name 为空 → 静默跳过

**路径处理**：catalog 中使用 `os.path.abspath()` 确保 `file_read` 可访问。

### agentmain.py 改动

```python
# 在 get_system_prompt() 末尾加一行
from skill_loader import get_skill_catalog

def get_system_prompt():
    # ...现有逻辑...
    prompt += get_skill_catalog()
    return prompt
```

改动量：1 行 import + 1 行调用。不影响 `agent_loop.py`、`ga.py`、`memory/`。

## Catalog 格式

```
## Available Skills

- **comet-open**: Comet 阶段 1：开启 (/Users/mac/.agents/skills/comet-open/SKILL.md)
- **comet-design**: Comet 阶段 2：深度设计 (/Users/mac/.agents/skills/comet-design/SKILL.md)
```

- 每个 skill 一行，含 name（粗体）、description、绝对路径
- 无 skill 时不输出此段
- 目录名 ≠ frontmatter name 时，catalog 使用 frontmatter name

## 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| YAML 解析 | 正则 | 零依赖，SKILL.md frontmatter 极简（仅 name + description） |
| Catalog 位置 | system_prompt 末尾 | 参考信息定位，不干扰核心指令 |
| 热加载策略 | 每次扫描 | 开销毫秒级，无状态同步问题 |
| Description 截断 | 不截断 | 17 skills ≈ 500-800 tokens，可接受 |
| 冲突策略 | 项目级覆盖 | 就近原则，符合路径优先级 |

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| Catalog token 膨胀（>50 skills） | 加 `max_skills` 配置或分桶 |
| 复杂 YAML frontmatter 解析失败 | 正则匹配不上 → 静默跳过，不影响其他 skill |
| 与 L3 记忆条目混淆 | 独立标题 `## Available Skills`，格式不同 |

## 测试策略

- **单元**：临时目录构造（空目录、合法/非法 SKILL.md、优先级覆盖）
- **集成**：启动 GA，检查 system_prompt 末尾
- **端到端**：agent 用 comet skill 完成操作
- **边界**：目录不存在、空 frontmatter、无 name、目录名≠frontmatter name
