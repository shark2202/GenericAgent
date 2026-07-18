"""Skill 自进化插件 — post-task skill distillation (hermes v1 主回路)。

被 plugins.hooks.discover_and_load() 自动加载（agentmain.py 启动时调用）。
spec: hermes/workflows/post-task-skill-distillation.md

事件：
  - agent_after（v1 主触发）：任务完成且 current_turn >= SKILL_DISTILL_MIN_TURNS
    且 GA_SKILL_EVOLUTION_ENABLED=1 → spawn daemon 线程跑 distill()。
    distill 是一次性自包含 LLM 调用（输入=history_info 快照+catalog），
    不共享 live session；产出 0/1 个 skill create/patch，经 do_skill_manage
    落盘（skill_write_origin='background_review'）。
  - tool_after / turn_after（v1.5，预留）：累计负信号（连续失败/ask_user 修正）
    到 handler._evolution_signal，任务结束时带信号触发（v1.5 才接 T9）。

Provenance：skill_write_origin ContextVar（foreground|background_review），镜像
hermes-agent。do_skill_manage 对 user/非 evolvable 技能一律拒写（D8 自主边界）。
熔断（D8）：单技能连续自动 patch >= MAX_AUTO_PATCH_PER_SKILL → 只产 Brief。
"""
import contextvars
import datetime
import json
import os
import re
import sys
import threading

import plugins.hooks as hooks
from skill_loader import _get_skills_catalog, script_dir as _skill_loader_script_dir

SKILL_DISTILL_MIN_TURNS = 6
MAX_AUTO_PATCH_PER_SKILL = 5        # 熔断：单技能连续自动 patch 上限（D8）

# provenance：标记本次 skill 写入来自前台（LLM 直接调 skill_manage）还是后台蒸馏
skill_write_origin = contextvars.ContextVar('skill_write_origin', default='foreground')

# 熔断计数：name -> 连续自动 patch 次数；前台修正（do_skill_manage 由 LLM 直调）或 revert 时重置
_auto_patch_counts = {}


def reset_auto_patch_count(name=None):
    """Reset the auto-patch counter for a skill (or all skills if name is None).

    Call this when a human-in-the-loop foreground edit or a revert happens,
    so the skill is no longer permanently breaker-locked by the background
    evolution loop.
    """
    global _auto_patch_counts
    if name is None:
        _auto_patch_counts = {}
    else:
        _auto_patch_counts.pop(name, None)


def _evolution_enabled():
    return os.environ.get('GA_SKILL_EVOLUTION_ENABLED') == '1'


def build_brief(action, name, why, path):
    """Brief 文案（push right）：进 turn%10 槽，给用户 approve/edit/revert。

    纯函数（无 handler 依赖），可单测。handler._build_skill_brief 与之本同构但用 self.cwd 做 relpath。
    """
    why_str = f" — {why}" if why else ""
    return f"\n📌[Skill蒸馏] {action} `{name}`{why_str} — file_read `{path}` 查看；.prev 已备份 [approve|edit|revert]"


def _drain(gen):
    """耗尽一个 generator 并返回其 StopIteration.value（do_skill_manage 返回 StepOutcome）。"""
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


def _parse_op(content):
    """从 LLM 文本回复中抽出第一个 {...} JSON 对象。返回 dict 或 None。"""
    if not content:
        return None
    m = re.search(r'\{[\s\S]*\}', content)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _append_fitness_log(name, action, source_turns, signal):
    """Append one structured entry to memory/skill_exp_<name>.md (§机制 7).

    Format (one line per event, append-only):
      - <ISO8601> | action=<action> | turns=<source_turns> | signal=<signal>
    """
    try:
        exp_dir = os.path.join(_skill_loader_script_dir, 'memory')
        os.makedirs(exp_dir, exist_ok=True)
        exp_path = os.path.join(exp_dir, f'skill_exp_{name}.md')
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        signal_str = json.dumps(signal, ensure_ascii=False) if signal is not None else 'null'
        line = f"- {ts} | action={action} | turns={source_turns} | signal={signal_str}\n"
        with open(exp_path, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception as e:
        sys.stderr.write(f"[skill_evolution] fitness log failed: {e}\n")


def _apply_op(handler, op, signal=None):
    """把蒸馏产出的单个 op 经 do_skill_manage 落盘（后台 origin + 熔断）。"""
    action = op.get('action')
    if action not in ('create', 'patch'):
        return
    name = (op.get('name') or '').strip()
    skill_md = op.get('skill_md', '')
    reason = op.get('reason', '')
    if not name or not skill_md:
        return
    # 熔断：单技能连续自动 patch 超限 → 只产 Brief 不落盘
    if action == 'patch' and _auto_patch_counts.get(name, 0) >= MAX_AUTO_PATCH_PER_SKILL:
        try:
            handler._pending_briefs.append(
                build_brief('patch(skipped-circuit)', name, reason, f'.agents/skills/{name}/SKILL.md'))
        except Exception:
            pass
        return
    args = {'action': action, 'name': name, 'skill_md': skill_md, 'reason': reason, 'dry_run': False}
    token = skill_write_origin.set('background_review')
    try:
        from agent_loop import get_tool
        fn = get_tool("skill_manage")
        outcome = _drain(fn(handler, args, None)) if fn else None
    finally:
        skill_write_origin.reset(token)
    status = getattr(outcome, 'data', None)
    if isinstance(status, dict) and status.get('status') == 'ok':
        if action == 'patch':
            _auto_patch_counts[name] = _auto_patch_counts.get(name, 0) + 1
        source_turns = getattr(handler, 'current_turn', 0)
        _append_fitness_log(name, action, source_turns, signal)


def distill(client, handler, signal=None):
    """一次性自包含 LLM 调用：从刚结束的任务结晶 0/1 个 Skill。

    不共享 live session：输入 = handler.history_info 快照 + key_info + catalog。
    """
    try:
        catalog, _ = _get_skills_catalog()
    except Exception:
        catalog = {}
    catalog_text = "\n".join(f"- {n}: {d}" for n, (d, _p) in catalog.items()) or "(none)"
    history = "\n".join(list(getattr(handler, 'history_info', []))[-40:])
    key_info = (getattr(handler, 'working', {}) or {}).get('key_info', '') or ''
    signal_note = f"\n[trigger_signal] {signal}" if signal else ""
    prompt = (
        "You are the self-evolution distiller. Crystallize at most ONE reusable Skill from the just-finished task. "
        "Only emit an op if a GENUINELY VERIFIED, REUSABLE pattern exists that is NOT already covered by an existing skill; otherwise return none.\n\n"
        f"<task_history>\n{history}\n</task_history>\n"
        f"<key_info>\n{key_info}\n</key_info>{signal_note}\n"
        f"<existing_skills>\n{catalog_text}\n</existing_skills>\n\n"
        "Respond with ONLY a JSON object, one of:\n"
        '  {"action": "none"}\n'
        '  {"action": "patch", "name": "<existing skill name>", "skill_md": "<FULL new SKILL.md>", "reason": "<short why>"}\n'
        '  {"action": "create", "name": "<slug>", "skill_md": "<FULL SKILL.md with frontmatter: name/description(<=120)/version:0.1/author:agent/evolvable:true/evolved_from:null/mcp_dependencies:[...]>", "reason": "<short why>"}\n'
        "Rules: skill_md MUST be a COMPLETE SKILL.md (frontmatter + body with >=1 '## ' heading). description <=120 chars. No trivia, no unverified info."
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        resp = client.chat(messages=messages, tools=[])
    except Exception:
        try:
            resp = client.chat(messages=messages)
        except Exception as e:
            sys.stderr.write(f"[skill_evolution] LLM call failed: {e}\n")
            return
    content = getattr(resp, 'content', '') or (resp if isinstance(resp, str) else '')
    op = _parse_op(content)
    if not op or op.get('action') == 'none':
        return
    try:
        _apply_op(handler, op, signal=signal)
    except Exception as e:
        sys.stderr.write(f"[skill_evolution] apply_op failed: {e}\n")


def _safe_distill(client, handler, signal):
    """distill 的线程入口：在新线程的 context 里设 background_review origin。"""
    token = skill_write_origin.set('background_review')
    try:
        distill(client, handler, signal=signal)
    except Exception as e:
        sys.stderr.write(f"[skill_evolution] distill thread failed: {e}\n")
    finally:
        skill_write_origin.reset(token)


@hooks.register('agent_after')
def _on_agent_after(ctx):
    """v1 主触发：任务完成 + 复杂度门控 + env 门控 → spawn 蒸馏线程。"""
    if not _evolution_enabled():
        return
    er = ctx.get('exit_reason') or {}
    if er.get('result') not in ('CURRENT_TASK_DONE', 'EXITED'):
        return
    handler = ctx.get('handler')
    client = ctx.get('client')
    if handler is None or client is None:
        return
    if getattr(handler, 'current_turn', 0) < SKILL_DISTILL_MIN_TURNS:
        return
    signal = getattr(handler, '_evolution_signal', None)
    threading.Thread(target=_safe_distill, args=(client, handler, signal), daemon=True).start()


@hooks.register('tool_after')
def _on_tool_after(ctx):
    """v1.5 信号（预留）：累计连续 code_run/file_patch 失败 -> handler._evolution_signal。"""
    # v1.5 占位：此处读 ctx['tool_name']/ctx['ret']，若 ret.data 形态为 error 且连续>=3，
    # 置 handler._evolution_signal={'type':'consecutive_failures',...}。
    # v1 不接 T9，保持 no-op 避免误触发。
    return


@hooks.register('turn_after')
def _on_turn_after(ctx):
    """v1.5 信号（预留）：捕获 ask_user 修正 -> handler._evolution_signal。"""
    # v1.5 占位：检测上一轮 ask_user 且用户回复与 agent 候选不同 -> 置 correction 信号。
    # v1 不接 T9，保持 no-op。
    return
