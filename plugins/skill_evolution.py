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
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import plugins.hooks as hooks
from skill_loader import _get_skills_catalog

SKILL_DISTILL_MIN_TURNS = 6
MAX_AUTO_PATCH_PER_SKILL = 5        # 熔断：单技能连续自动 patch 上限（D8）
MAX_CHANGES_PER_DISTILL = 3        # 熔断：单次蒸馏改技能上限（D8；v1 一次只产 1 op）

# ── Skill scoring gate (hermes-isolated-skill-scorer) ─────────────
# Design: docs/superpowers/specs/2026-07-19-hermes-isolated-skill-scorer-design.md §2
# 闸门插在 distill() 的 _parse_op 与 _apply_op 之间(L148 / L152)。
# gate off (GA_SKILL_EVOLUTION_ENABLED != '1' 或 GA_SKILL_SCORER no-op) → v1 行为。
GA_SKILL_SCORER_THRESHOLD = int(os.environ.get("GA_SKILL_SCORER_THRESHOLD", "60"))
GA_SKILL_SCORER_TIMEOUT = int(os.environ.get("GA_SKILL_SCORER_TIMEOUT", "600"))


class VerdictKind(Enum):
    PASS = "pass"
    REJECT = "reject"
    REVISE = "revise"


@dataclass
class Verdict:
    score: int                          # 0-100
    verdict: VerdictKind
    dims: dict                          # {reusability, verifiedness, non_redundancy}: 0-100
    rationale: str
    source: str = "inprocess"           # "inprocess" | "subagent" | "degraded"  (留痕用)


class Scorer(Protocol):
    """打分器接口。score(op, skill_md, history, catalog) -> Verdict。

    op: distill() _parse_op 产出的 dict(action/name/skill_md/reason)。
    skill_md: 候选 SKILL.md 全文(op['skill_md'])。
    history: task history_info 快照(列表或拼接串;评判官看客观历史,非 generator 判断)。
    catalog: 现有技能 catalog 文本(查冗余用)。
    """

    def score(self, op, skill_md: str, history, catalog: str) -> Verdict: ...


# 打分对象 JSON schema(传 SubagentScorer.run_single(schema=...) / InProcessScorer 解析校验)
# v1 required 锁 3 维(OQ4 决议);dims.additionalProperties:false 严格锁;
# 后续加维度需先改 schema,本 change 不动。
SCORE_SCHEMA = {
    "type": "object",
    "required": ["score", "verdict", "dims", "rationale"],
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "enum": ["pass", "reject", "revise"]},
        "dims": {
            "type": "object",
            "required": ["reusability", "verifiedness", "non_redundancy"],
            "properties": {
                "reusability": {"type": "integer", "minimum": 0, "maximum": 100},
                "verifiedness": {"type": "integer", "minimum": 0, "maximum": 100},
                "non_redundancy": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "additionalProperties": False,
        },
        "rationale": {"type": "string"},
    },
    "additionalProperties": False,
}


class ScorerDegraded(Exception):
    """打分器降级信号(SubagentScorer failed/timed_out,或 InProcess 校验失败)。"""


SCORER_SYSTEM_PROMPT = (
    "你是技能评审官(scorer),只读候选 SKILL.md + task 历史 + 现有技能 catalog, "
    "按 reusability(可复用性)/verifiedness(已验证性)/non_redundancy(非冗余性)三维度打分(0-100)。 "
    "你不生成 skill,只评估候选 skill_md 是否值得落盘。 "
    '返回 JSON: {"score":0-100, "verdict":"pass|reject|revise", '
    '"dims":{"reusability":0-100,"verifiedness":0-100,"non_redundancy":0-100}, "rationale":"..."}。 '
    "score>=阈值且 verdict=pass 才放行;reject/revise 或 score<阈值 → 拒绝。"
)


def _extract_json(content: str):
    """容错从 LLM 文本抽 JSON 对象(支持 ```json 代码块 / 裸 JSON)。返回 dict 或 None。"""
    if not content:
        return None
    # 先尝试抽 ```json ... ``` 代码块
    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
    if m:
        candidate = m.group(1)
    else:
        m = re.search(r'\{[\s\S]*\}', content)
        if not m:
            return None
        candidate = m.group(0)
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _validate_obj(obj, schema: dict) -> None:
    """手写最小 schema 校验(覆盖 type/required/properties/enum/minimum/maximum/additionalProperties)。

    复用 subagent_manager 风格(零新依赖,jsonschema 不在 deps,YAGNI)。
    校验失败 → raise ScorerDegraded。
    """
    if obj is None:
        raise ScorerDegraded("score object is None")
    if schema.get("type") == "object" and not isinstance(obj, dict):
        raise ScorerDegraded("expected object")
    for req in schema.get("required", []):
        if req not in obj:
            raise ScorerDegraded(f"missing required field: {req}")
    for k, subschema in schema.get("properties", {}).items():
        if k not in obj:
            continue
        v = obj[k]
        t = subschema.get("type")
        if t == "integer" and not isinstance(v, int):
            raise ScorerDegraded(f"{k}: expected integer")
        if t == "string" and not isinstance(v, str):
            raise ScorerDegraded(f"{k}: expected string")
        if t == "object" and not isinstance(v, dict):
            raise ScorerDegraded(f"{k}: expected object")
        if "enum" in subschema and v not in subschema["enum"]:
            raise ScorerDegraded(f"{k}: {v!r} not in enum {subschema['enum']}")
        if "minimum" in subschema and isinstance(v, (int, float)) and v < subschema["minimum"]:
            raise ScorerDegraded(f"{k}: {v} < minimum {subschema['minimum']}")
        if "maximum" in subschema and isinstance(v, (int, float)) and v > subschema["maximum"]:
            raise ScorerDegraded(f"{k}: {v} > maximum {subschema['maximum']}")
        if t == "object":
            _validate_obj(v, subschema)  # 递归 dims
    if schema.get("additionalProperties") is False:
        allowed = set(schema.get("properties", {}).keys())
        extra = set(obj.keys()) - allowed
        if extra:
            raise ScorerDegraded(f"additional properties not allowed: {extra}")


def _build_scorer_user_msg(skill_md: str, history, catalog: str) -> str:
    """构造评判官 user message。禁含 op['reason'] / generator CoT(D3)。

    history 是任务客观历史(handler.history_info 快照),非 generator 判断,可接受。
    """
    if isinstance(history, (list, tuple)):
        history_text = "\n".join(str(h) for h in list(history)[-40:])
    else:
        history_text = str(history)
    return (
        f"# 候选技能 SKILL.md\n\n{skill_md}\n\n"
        f"---\n\n# 任务历史快照(最近 40 条,客观事实)\n\n{history_text}\n\n"
        f"---\n\n# 现有技能 catalog(查冗余用)\n\n{catalog}\n\n"
        "请按三维度打分并返回 JSON(只返回 JSON,无其他文本)。"
    )


class InProcessScorer:
    """同进程二次 LLM 打分(弱独立兜底,OQ5 决议)。

    弱独立性声明:同 client(同 session/backend.history),独立性靠 fresh messages + distinct
    prompt role(system role = scorer 职责)软保证。这是 "better than no gate",非 "true independence";
    SubagentScorer 才是真独立。与 v1 既有的 backend.history 污染行为一致(distill generator 调用本就 append)。
    降级时 source="degraded" 留痕。
    """

    def __init__(self, client):
        self._client = client

    def score(self, op, skill_md, history, catalog) -> Verdict:
        messages = [
            {"role": "system", "content": SCORER_SYSTEM_PROMPT},
            {"role": "user", "content": _build_scorer_user_msg(skill_md, history, catalog)},
        ]
        try:
            resp = self._client.chat(messages=messages, tools=[])
        except Exception as e:
            sys.stderr.write(f"[scorer] inprocess chat failed: {e}\n")
            return Verdict(score=0, verdict=VerdictKind.REJECT, dims={},
                           rationale=f"chat error: {e}", source="degraded")
        content = getattr(resp, 'content', '') or (resp if isinstance(resp, str) else '')
        obj = _extract_json(content)
        try:
            _validate_obj(obj, SCORE_SCHEMA)
        except ScorerDegraded as e:
            return Verdict(score=0, verdict=VerdictKind.REJECT, dims={},
                           rationale=f"invalid score obj: {e}", source="degraded")
        return Verdict(
            score=obj["score"],
            verdict=VerdictKind(obj["verdict"]),
            dims=obj["dims"],
            rationale=obj["rationale"],
            source="inprocess",
        )


def _build_scorer_desc(skill_md: str, history, catalog: str) -> str:
    """构造评判子 agent 的 desc(D3 独立性硬保证)。

    候选 SKILL.md 全文 + history_info[-40:] 快照 + catalog。
    **禁含** op['reason'] 与任何 generator prompt 片段。
    history_info 是任务客观历史(非 generator 判断),可接受(D3 Risks)。
    """
    if isinstance(history, (list, tuple)):
        history_text = "\n".join(str(h) for h in list(history)[-40:])
    else:
        history_text = str(history)
    parts = [
        "# 候选技能 SKILL.md\n\n" + skill_md,
        "# 任务历史快照(最近 40 条,客观事实)\n\n" + history_text,
        "# 现有技能 catalog(查冗余用)\n\n" + catalog,
    ]
    desc = "\n\n---\n\n".join(parts)
    # 硬断言:desc 不含生成器 reason 片段(单测覆盖见 test_subagent_scorer_desc_actually_excludes_reason)
    # 注:此处不强制 assert,因 reason 关键词可能与 skill_md 正文巧合;调用方保证 op['reason'] 不入参。
    return desc


class SubagentScorer:
    """隔离子 agent 打分(真独立,independence-by-construction)。

    经 handler._subagent_mgr.run_single() 派发:独立 git worktree(--detach)+ 独立 LLM session
    (subprocess 起 agentmain.py,GA_TASK_MODE=isolated)。评估器看不到生成器 reason/CoT(D3)。
    """

    def __init__(self, subagent_mgr):
        self._mgr = subagent_mgr

    def score(self, op, skill_md, history, catalog) -> Verdict:
        # D3:desc 不含 op['reason'](_build_scorer_desc 不接 reason 参数,硬保证)
        desc = _build_scorer_desc(skill_md, history, catalog)
        res = self._mgr.run_single(
            desc=desc,
            schema=SCORE_SCHEMA,
            tools_subset=["file_read"],     # D3/OQ1:只读;submit_result 由 _apply_task_mode 强制注入
            base_ref=None,                  # OQ2:None → 默认 dev HEAD
            timeout_s=GA_SKILL_SCORER_TIMEOUT,
        )
        if getattr(res, "state", None) in ("failed", "timed_out"):
            raise ScorerDegraded(getattr(res, "state", "unknown"))
        # 二次防御性校验(run_single 已 validate,但兜底)
        try:
            _validate_obj(res.result, SCORE_SCHEMA)
        except ScorerDegraded:
            raise
        r = res.result
        return Verdict(
            score=r["score"],
            verdict=VerdictKind(r["verdict"]),
            dims=r["dims"],
            rationale=r["rationale"],
            source="subagent",
        )


# provenance：标记本次 skill 写入来自前台（LLM 直接调 skill_manage）还是后台蒸馏
skill_write_origin = contextvars.ContextVar('skill_write_origin', default='foreground')

# 熔断计数：name -> 连续自动 patch 次数；前台修正（do_skill_manage 由 LLM 直调）时重置
_auto_patch_counts = {}
_consecutive_auto_distills = 0


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


def _apply_op(handler, op):
    """把蒸馏产出的单个 op 经 do_skill_manage 落盘（后台 origin + 熔断）。"""
    global _consecutive_auto_distills
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
        _consecutive_auto_distills += 1
    else:
        # 落盘失败/校验失败：不计入连续自动 patch
        _consecutive_auto_distills = 0


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
        _apply_op(handler, op)
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
