"""MCP 蒸馏插件 — tool_after hook 记录 mcp_call 调用到 L2 日志。

兑现 spec skill-discovery:85 "agent MAY initiate start_long_term_update to persist
the knowledge to L2 memory" 的前置条件：mcp_call 调用记录持久化，供 LLM 蒸馏时引用。

不自动蒸馏——蒸馏需 LLM 判断 lasting value（spec:87-90 "MCP call returns ephemeral
data → no L2 memory update triggered"）。本插件只做记录，不做判断。
"""
import json
import os
import time

import plugins.hooks as hooks

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_PATH = os.path.join(_PROJECT_ROOT, 'memory', 'mcp_call_log.md')
_MAX_LOG_ENTRIES = 200


def _append_entry(entry):
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    lines = []
    if os.path.isfile(_LOG_PATH):
        with open(_LOG_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    if not lines or not lines[0].startswith('# '):
        lines = ['# MCP Call Log\n\n'] + lines
    lines.append(entry + '\n')
    data_lines = [l for l in lines if l.startswith('- ')]
    if len(data_lines) > _MAX_LOG_ENTRIES:
        keep = data_lines[-_MAX_LOG_ENTRIES:]
        lines = [l for l in lines if not l.startswith('- ')] + keep
    with open(_LOG_PATH, 'w', encoding='utf-8') as f:
        f.writelines(lines)


@hooks.register('tool_after')
def _on_mcp_call_after(ctx):
    """tool_after hook：mcp_call 完成后追加调用记录到 memory/mcp_call_log.md。"""
    if ctx.get('tool_name') != 'mcp_call':
        return
    args = ctx.get('args', {})
    ret = ctx.get('ret')
    server = args.get('server', '?')
    tool = args.get('tool', '?')
    success = ret is not None and getattr(ret, 'data', None) is not None
    ts = time.strftime('%Y-%m-%dT%H:%M:%S')
    call_args = args.get('arguments', {})
    entry = (
        f"- {ts} | {server}/{tool} | {'OK' if success else 'FAIL'} | "
        f"args={json.dumps(call_args, ensure_ascii=False)[:200]}"
    )
    try:
        _append_entry(entry)
    except Exception:
        pass
