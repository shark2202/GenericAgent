# Design: Skill 懒加载 + mtime 缓存

## 架构概览

对齐 [agentskills.io spec](https://agentskills.io/specification) 的 **progressive disclosure 三层模型**：

| 层 | spec 规范 | GenericAgent 现状 | 本次改动 |
|---|---|---|---|
| Metadata（~100 tokens） | 启动时加载所有 skill 的 name+description | L1 段只存 name（token 预算设计，偏离 spec） | 保持现状（见权衡说明） |
| Instructions（<5000 tokens） | skill 激活时加载完整 SKILL.md body | 无按需接口，AI 直接 file_read 全文 | 新增 `get_skill_detail` 返回摘要 + path，AI 再 file_read 全文 |
| Resources（按需） | scripts/references/assets 按需加载 | 无目录概览接口 | `get_skill_detail` 返回 has_scripts/references/assets |

## 组件设计

### 1. `get_skill_detail(name)` — skill_loader.py

**返回**（spec Metadata 层详情 + Resources 目录概览 + Instructions 入口）：
```
{name, description, license?, compatibility?, metadata?, allowed_tools?, skill_md_path, has_scripts, has_references, has_assets}
```
- 可选字段（license/compatibility/metadata/allowed_tools）仅在该 skill frontmatter 中存在时返回。
- `skill_md_path`：AI 据此 file_read 全文（spec Instructions 层）。
- `has_scripts/references/assets`：AI 据此知道有无可按需加载的资源（spec Resources 层）。

**实现**：
- 从 `_get_skills_catalog()` 拿 (desc, path)（共享 mtime 缓存，不重复扫描）。
- 读该 SKILL.md frontmatter 解析全字段（`_parse_full_frontmatter`，按需读单个文件，可接受）。
- stat 三个可选子目录。

**错误处理**：
- name 不存在 → `{'status': 'error', 'msg': "Skill '...' not found.", 'available': [sorted names]}`
- 无 frontmatter → 该 skill 不会被 `_discover_skills` 收入 catalog → 走 not-found 分支。
- IO 错 → 异常由 `do_get_skill_detail` 捕获返回 error。

### 2. `_get_skills_catalog()` — mtime 双层缓存

**模块级缓存**：
- `_SKILL_CATALOG_CACHE`：`{name: (desc, path)}` 或 None
- `_SKILL_MTIME_CACHE`：`{'roots': {root_dir: mtime}, 'files': {skill_md_path: mtime}}`

**逻辑**：
- 冷启动（cache None）→ 全量 `_discover_skills()` → 填充缓存 → 返回 `(catalog, fresh=False)`。
- 非冷启动 → `_check_skill_mtimes()`：
  - 当前 `_skill_roots()` 集合 ≠ 缓存 roots → 变化（新根出现/消失）。
  - 任一 root mtime 变化 → 变化（skill 目录增删）。
  - 任一 SKILL.md mtime 变化或 stat 失败 → 变化（内容修改/删除）。
  - 全一致 → `fresh=True`，返回缓存 catalog。
- 变化 → 全量 rescan → 更新缓存 → 返回 `(catalog, fresh=False)`。
- fail-safe：stat 失败 → 视为变化触发 rescan。

**`sync_skills_to_l1` 改动**：改用 `_get_skills_catalog()` 获取 catalog。**始终 rewrite L1**（不跳过）——因为 hot/cold 分类依赖 experience 文件（`memory/skill_exp_*.md`），这些文件不被 mtime 缓存跟踪。缓存只跳过昂贵的 rescan（47 目录扫描 + 47 frontmatter 解析），L1 rewrite（单文件写）成本低且保证 hot/cold 正确。

**`get_skill_detail` 也用 `_get_skills_catalog()`**：共享缓存，不重复扫描。

### 3. `do_get_skill_detail` — ga.py

Agent 工具入口，仿 `do_file_read` 模式：
- `yield` 状态文本 + JSON 格式的 detail。
- 成功时 `next_prompt` 追加提示"如需完整内容请用 file_read 读取 skill_md_path"。
- 异常 → `StepOutcome({'status':'error','msg':...})`。

### 4. tools_schema 双文件同步

`assets/tools_schema.json` + `assets/tools_schema_cn.json` 均新增 `get_skill_detail` 定义（仿 `file_read` schema 格式，`name` 参数 required）。

## 权衡说明

### L1 段保持只存 name（不对齐 spec 存 name+description）

agentskills.io spec 的 Metadata 层规范要求启动时加载所有 skill 的 name+description。GenericAgent 的 L1 段只存 name（cold skills 逗号列表），偏离 spec。

**保持现状的理由**：
1. 现有 token 预算设计有 `test_token_budget` 测试保护（47×name≈200 tokens vs 47×(name+desc)≈1500 tokens）。
2. 与本次"懒加载"主题一致：L1 最小化（只存 name），`get_skill_detail` 按需补 description，比 spec 更激进的渐进式披露。
3. 最小改动原则：改 L1 格式会破坏现有测试和 token 预算设计，超出本次 change 范围。

`get_skill_detail` 填补了"AI 不知道 description"的缺口：AI 对 L1 中感兴趣的 skill 名调 `get_skill_detail` 取 description，再决定是否 file_read 全文。

### mtime 缓存不跳过 L1 rewrite

如上所述，hot/cold 分类依赖 experience 文件，不被 mtime 缓存跟踪。若跳过 rewrite，增删 experience 文件后 L1 的 hot/cold 分类会过期。因此缓存只跳过 rescan（主要 IO 开销），始终 rewrite L1（单文件写，低成本，保证正确）。

## 顺带发现（不本次修）

`tools_schema.json`（10 工具，含 mcp_call）与 `tools_schema_cn.json`（9 工具，缺 mcp_call）不同步。根因：双 JSON 手工维护无单一数据源/生成机制。本次 `get_skill_detail` 已双文件同步添加，避免重蹈覆辙。mcp_call 不同步问题应单独 change 修复。
