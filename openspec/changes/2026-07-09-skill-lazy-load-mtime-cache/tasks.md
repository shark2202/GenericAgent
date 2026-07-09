# Tasks: Skill 懒加载 + mtime 缓存

- [x] skill_loader.py: 新增 `_skill_roots()`（重构 `_discover_skills` 共享根逻辑）
- [x] skill_loader.py: 新增 mtime 双层缓存（`_get_skills_catalog`/`_compute_skill_mtimes`/`_check_skill_mtimes`/`_reset_skill_cache`）
- [x] skill_loader.py: 新增 `_parse_full_frontmatter`（解析 spec 全字段）
- [x] skill_loader.py: 新增 `get_skill_detail(name)`
- [x] skill_loader.py: `sync_skills_to_l1` 改用 `_get_skills_catalog`（跳过 rescan，始终 rewrite L1）
- [x] ga.py: import `get_skill_detail` + 新增 `do_get_skill_detail`
- [x] assets/tools_schema.json + tools_schema_cn.json: 双文件同步加 `get_skill_detail` 定义
- [x] tests/test_skill_loader_l1.py: autouse fixture 重置缓存 + get_skill_detail 3 测试 + mtime 缓存 3 测试
- [x] 验证: pytest 26/26 PASS, ruff 无新违规
- [x] 勾选完成 + 提交
