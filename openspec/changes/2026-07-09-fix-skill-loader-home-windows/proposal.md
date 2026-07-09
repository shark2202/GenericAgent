# Proposal: fix-skill-loader-home-windows

## 问题描述

`memory/global_mem_insight.txt` 的 skill 自动注入段（`<!-- auto-skills-start -->` / `<!-- auto-skills-end -->` 之间）始终为空，skill 信息从未出现在 L1 记忆索引中。Agent 因此无法通过 L1 发现可用 skill。

实际验证：`C:\Users\Administrator\.agents\skills\` 下存在 47 个 skill 目录，但 `skill_loader._discover_skills()` 返回空 catalog，`global_mem_insight.txt` 末尾 marker 段为空白。

## 根因分析

`skill_loader.py:68` 使用 `os.environ.get('HOME', '')` 解析用户主目录：

```python
home = os.environ.get('HOME', '')          # skill_loader.py:68
user_root = os.path.join(home, '.agents', 'skills') if home else None  # :69
```

在 Windows 上 `HOME` 环境变量**通常未设置**（Windows 使用 `USERPROFILE`），实测当前环境 `HOME=`（空）、`USERPROFILE=C:\Users\Administrator`。因此 `home=''` → `user_root=None` → **用户级 skill 目录从不扫描**。项目级 `D:\GenericAgent\.agents\skills` 不存在，结果 catalog 恒为空。

`ga.py:605` 调用 `sync_skills_to_l1()` 时失败被 `except: pass`（ga.py:606-607）静默吞掉，且因 catalog 本身为空，sync 正常返回但写入空 marker 段——无异常、无日志、无 skill。

## 修复目标

使 `_discover_skills()` 在 Windows 上能正确回退到 `USERPROFILE`，从而发现用户级 skill 并注入 L1。不改变 Unix 行为，不新增 public API，不涉及架构调整。

## 影响范围

- 单文件单函数：`skill_loader.py:_discover_skills`（1 行改动）
- 补 1 个不 mock `_discover_skills` 的 Windows 路径解析测试，填补验收盲区

## 为什么之前验收没发现

`tests/test_skill_loader_l1.py` 全部用例要么 `patch.object(skill_loader, '_discover_skills')` mock 掉真实扫描，要么 `patch.dict(os.environ, {"HOME": ...})` 显式设置 HOME——**无任何用例覆盖 HOME 未设置的 Windows 真实环境**。mock 掩盖了路径解析缺陷，导致 59/59 假绿。
