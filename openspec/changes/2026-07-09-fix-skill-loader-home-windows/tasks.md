## 1. 修复

- [x] 1.1 修复 `skill_loader.py:_discover_skills` 的 home 解析，HOME→USERPROFILE→expanduser('~') 回退
- [x] 1.2 补测试 `tests/test_skill_loader_l1.py`：HOME 未设置 + USERPROFILE 存在时 `_discover_skills` 能发现 skill（不 mock `_discover_skills`）

## 2. 验证

- [x] 2.1 运行 `pytest tests/test_skill_loader_l1.py` 全绿
- [x] 2.2 运行 `ruff check skill_loader.py tests/test_skill_loader_l1.py` 无新违规
- [x] 2.3 实跑 `python -c "import skill_loader; print(len(skill_loader._discover_skills()))"` 确认 Windows 实环境 catalog 非空
