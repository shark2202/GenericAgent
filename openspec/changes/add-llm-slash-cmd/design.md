## Design: `/llm` Slash Command

### Approach

在 `agentmain.py` 的 `_handle_slash_cmd()` 方法中新增 `elif cmd == "llm":` 分支，复用现有 `list_llms()` 和 `next_llm()` 函数。

### Implementation Details

```python
elif cmd == "llm":
    models = list_llms()
    if not args or args.strip() == "":
        # List all models with current highlighted
        active = models[0] if models else None
        for i, m in enumerate(models):
            marker = " ✓" if m == active else ""
            print(f"  [{i}] {m}{marker}")
        print(f"\n当前: {active}")
    else:
        # Switch by index
        try:
            idx = int(args.strip())
            if 0 <= idx < len(models):
                for _ in range(idx):
                    next_llm()
                print(f"已切换到: {list_llms()[0]}")
            else:
                print(f"索引超出范围 (0-{len(models)-1})")
        except ValueError:
            print("用法: /llm [索引号]")
```

### Key Decisions

1. **索引切换而非名称切换**：`next_llm()` 是循环式 API，通过多次调用前进到目标索引，简单可靠
2. **无参数=查看**：与 TUI v1/v2 `/llm` 行为对齐
3. **不修改公共 API**：完全复用现有函数，零新导出

### Affected Files

| File | Change Type | Description |
|------|------------|-------------|
| `agentmain.py` | 修改 | `_handle_slash_cmd()` 新增 `elif cmd == "llm":` 分支 |

### No New Dependencies

无需新增任何 import——`list_llms` 和 `next_llm` 已在模块顶层 import。
