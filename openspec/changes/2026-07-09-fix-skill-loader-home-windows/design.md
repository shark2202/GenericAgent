# Design: fix-skill-loader-home-windows

## 修复方案（hotfix 单方案）

将 `skill_loader.py:68` 的 home 解析改为回退链，兼容 Windows：

```python
# 修复前
home = os.environ.get('HOME', '')

# 修复后
home = os.environ.get('HOME') or os.environ.get('USERPROFILE') or os.path.expanduser('~')
```

回退语义：
1. `HOME` 有值 → 用 HOME（Unix 行为不变，向后兼容）
2. `HOME` 空 → `USERPROFILE`（Windows 显式主目录，可测试、不依赖 expanduser 平台行为）
3. 两者皆无 → `os.path.expanduser('~')`（最终回退，Windows 上亦解析到 USERPROFILE）

## 不改动的部分

- `_scan_dir` / `_parse_skill_frontmatter` / `sync_skills_to_l1` / marker 机制：均正常，无需改动
- `ga.py:605` 调用点与 `except: pass`：sync 本身无异常（catalog 空不抛错），无需改动
- 项目级扫描 `$CWD/.agents/skills`：逻辑正确，保持不变

## 测试策略

补 1 个不 mock `_discover_skills` 的真实路径解析测试，覆盖 HOME 未设置 + USERPROFILE 存在的 Windows 场景：

- 创建 `tmp_path/home/.agents/skills/win-skill/SKILL.md`（带合法 frontmatter）
- `monkeypatch.delenv('HOME', raising=False)` + `monkeypatch.setenv('USERPROFILE', str(tmp_path/home))`
- 项目级指向空目录，排除项目级干扰
- 断言 `_discover_skills()` catalog 含 `win-skill`

此测试在修复前应失败（HOME 空 → catalog 空），修复后通过——构成回归保护，防止再次假绿。

## 风险

极低。回退链只在 HOME 为空时生效，Unix 环境下 HOME 必有值，行为完全不变。Windows 环境下从"扫不到"变为"扫得到"，是纯增益。
