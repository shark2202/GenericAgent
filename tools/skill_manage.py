"""skill_manage tool — self-evolution skill CRUD (create/patch/retire/list_evolvable).

Migrated from GenericAgentHandler.do_skill_manage so it self-registers via
register_tool and dispatches through the registry-track, demonstrating the
drop-in extensibility loop. Behavior unchanged (38 skill tests guard it)."""
import os
import re
import tempfile

from agent_loop import StepOutcome, register_tool
from plugins.skill_evolution import reset_auto_patch_count, skill_write_origin
from skill_loader import (
    _get_skills_catalog,
    _reset_skill_cache,
    backup_skill_prev,
    get_skill_detail,
    parse_provenance,
    revert_skill_prev,
    sync_skills_to_l1,
    validate_skill,
)


def _maybe_reset_on_foreground(name):
    """Reset auto-patch counter when a human-in-the-loop foreground edit succeeds."""
    try:
        if skill_write_origin.get() != 'background_review':
            reset_auto_patch_count(name)
    except Exception:
        pass


@register_tool("skill_manage")
def skill_manage(handler, args, response):
    '''自进化：管理 agent 自己的 skill（create/patch/retire/list_evolvable）。
    patch/retire 仅作用于 author=agent 且 evolvable 的技能；用户创作技能只读。
    patch/create 写前自动 .prev 备份；写动作需 GA_SKILL_EVOLUTION_ENABLED=1（list_evolvable/dry_run 豁免）。'''
    action = args.get('action', '')
    name = args.get('name', '')
    skill_md = args.get('skill_md', '')
    reason = args.get('reason', '')
    dry_run = bool(args.get('dry_run', False))
    idx = args.get('_index', 0)
    yield f"\n[Action] skill_manage: {action} {name}\n"

    if action == 'list_evolvable':
        try:
            catalog, _ = _get_skills_catalog()
        except Exception as e:
            return StepOutcome({'status': 'error', 'msg': f'catalog: {e}'}, next_prompt="\n")
        owned = []
        for nm, (_desc, path) in catalog.items():
            try:
                prov = parse_provenance(path)
            except Exception:
                continue
            if prov.get('author') == 'agent' and prov.get('evolvable'):
                owned.append({'name': nm, 'version': prov.get('version'), 'evolved_from': prov.get('evolved_from'), 'path': path})
        yield f"[Skill Manage] {len(owned)} evolvable agent skill(s)\n"
        return StepOutcome({'status': 'ok', 'evolvable_skills': owned, 'count': len(owned)}, next_prompt=handler._get_anchor_prompt(skip=idx > 0))

    if action not in ('create', 'patch', 'retire'):
        return StepOutcome({'status': 'error', 'msg': f'unknown action: {action}'}, next_prompt="\n")

    if os.environ.get('GA_SKILL_EVOLUTION_ENABLED') != '1':
        msg = "skill_manage writes require GA_SKILL_EVOLUTION_ENABLED=1 (opt into self-evolution). list_evolvable/dry_run are exempt."
        yield f"[Skill Manage] {msg}\n"
        return StepOutcome({'status': 'disabled', 'msg': msg}, next_prompt="\n")

    if action == 'create':
        if not name:
            return StepOutcome({'status': 'error', 'msg': 'create requires name'}, next_prompt="\n")
        if not skill_md:
            return StepOutcome({'status': 'error', 'msg': 'create requires skill_md'}, next_prompt="\n")
        try:
            catalog, _ = _get_skills_catalog()
        except Exception:
            catalog = {}
        if name in catalog:
            return StepOutcome({'status': 'error', 'msg': f'skill {name} already exists; use action=patch'}, next_prompt="\n")
        if dry_run:
            ok, why = _validate_skill_content(skill_md)
            return StepOutcome({'status': 'ok' if ok else 'invalid', 'reason': why, 'dry_run': True}, next_prompt="\n")
        skills_root = os.path.abspath(os.path.join(handler.cwd, '.agents', 'skills'))
        skill_dir = os.path.join(skills_root, name)
        path = os.path.join(skill_dir, 'SKILL.md')
        try:
            os.makedirs(skill_dir, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(skill_md)
        except Exception as e:
            return StepOutcome({'status': 'error', 'msg': f'write: {e}'}, next_prompt="\n")
        ok, why = validate_skill(path)
        if not ok:
            try:
                os.remove(path)
            except Exception:
                pass
            try:
                os.rmdir(skill_dir)
            except Exception:
                pass
            _reset_skill_cache()
            return StepOutcome({'status': 'invalid', 'reason': why}, next_prompt="\n")
        _reset_skill_cache()
        try:
            sync_skills_to_l1()
        except Exception:
            pass
        _maybe_reset_on_foreground(name)
        yield f"[Skill Manage] created {name} at {path}\n"
        handler._pending_briefs.append(_build_skill_brief('create', name, reason, path, handler.cwd))
        return StepOutcome({'status': 'ok', 'action': 'create', 'name': name, 'path': path}, next_prompt=handler._get_anchor_prompt(skip=idx > 0))

    # patch / retire: operate on the existing skill at its catalog path
    try:
        catalog, _ = _get_skills_catalog()
    except Exception as e:
        return StepOutcome({'status': 'error', 'msg': f'catalog: {e}'}, next_prompt="\n")
    if name not in catalog:
        return StepOutcome({'status': 'error', 'msg': f'skill {name} not found'}, next_prompt="\n")
    _desc, path = catalog[name]
    try:
        prov = parse_provenance(path)
    except Exception as e:
        return StepOutcome({'status': 'error', 'msg': f'provenance: {e}'}, next_prompt="\n")

    # provenance gate: user-authored or non-evolvable skills are read-only
    if prov.get('author') == 'user' or not prov.get('evolvable'):
        suggestion = f"[suggest] {name} is author={prov.get('author')}/evolvable={prov.get('evolvable')}; not editable via skill_manage. Draft the proposed SKILL.md and present it as a suggestion to the user instead of overwriting."
        yield suggestion + "\n"
        return StepOutcome({'status': 'protected', 'name': name, 'author': prov.get('author'), 'msg': suggestion}, next_prompt="\n")

    if action == 'retire':
        backup_skill_prev(path)
        try:
            with open(path, encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            return StepOutcome({'status': 'error', 'msg': f'read: {e}'}, next_prompt="\n")
        new_content = _set_frontmatter_flag(content, 'evolvable', 'false')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)
        except Exception as e:
            revert_skill_prev(path)
            reset_auto_patch_count(name)
            return StepOutcome({'status': 'error', 'msg': f'write: {e}'}, next_prompt="\n")
        _reset_skill_cache()
        try:
            sync_skills_to_l1()
        except Exception:
            pass
        _maybe_reset_on_foreground(name)
        yield f"[Skill Manage] retired {name} (evolvable=false, file kept)\n"
        handler._pending_briefs.append(_build_skill_brief('retire', name, reason, path, handler.cwd))
        return StepOutcome({'status': 'ok', 'action': 'retire', 'name': name, 'path': path}, next_prompt=handler._get_anchor_prompt(skip=idx > 0))

    # action == 'patch'
    if not skill_md:
        return StepOutcome({'status': 'error', 'msg': 'patch requires skill_md (full replacement)'}, next_prompt="\n")
    if dry_run:
        ok, why = _validate_skill_content(skill_md)
        return StepOutcome({'status': 'ok' if ok else 'invalid', 'reason': why, 'dry_run': True}, next_prompt="\n")
    backup_skill_prev(path)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(skill_md)
    except Exception as e:
        return StepOutcome({'status': 'error', 'msg': f'write: {e}'}, next_prompt="\n")
    ok, why = validate_skill(path)
    if not ok:
        revert_skill_prev(path)
        reset_auto_patch_count(name)
        _reset_skill_cache()
        return StepOutcome({'status': 'invalid', 'reason': why, 'reverted': True}, next_prompt="\n")
    # Ensure the patched skill is still loadable through the catalog (R9).
    _reset_skill_cache()
    detail = get_skill_detail(name)
    if detail.get('status') == 'error':
        revert_skill_prev(path)
        reset_auto_patch_count(name)
        return StepOutcome({'status': 'invalid', 'reason': 'skill no longer loadable after patch', 'reverted': True}, next_prompt="\n")
    try:
        sync_skills_to_l1()
    except Exception:
        pass
    _maybe_reset_on_foreground(name)
    yield f"[Skill Manage] patched {name} (.prev saved)\n"
    handler._pending_briefs.append(_build_skill_brief('patch', name, reason, path, handler.cwd))
    return StepOutcome({'status': 'ok', 'action': 'patch', 'name': name, 'path': path}, next_prompt=handler._get_anchor_prompt(skip=idx > 0))


def _validate_skill_content(skill_md):
    '''Validate SKILL.md content without touching the target path (dry_run).'''
    fd, tmp = tempfile.mkstemp(suffix='.md')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(skill_md)
        return validate_skill(tmp)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _set_frontmatter_flag(content, key, value):
    '''Set a frontmatter field to `value` (replace if present, else append). No-op without frontmatter.'''
    if not content.startswith('---'):
        return content
    end = content.find('---', 3)
    if end == -1:
        return content
    fm = content[3:end]
    pat = re.compile(r'^' + re.escape(key) + r':\s*.*$', re.MULTILINE)
    if pat.search(fm):
        fm = pat.sub(f'{key}: {value}', fm)
    else:
        fm = fm.rstrip('\n') + f'\n{key}: {value}\n'
    return '---' + fm + content[end:]


def _build_skill_brief(action, name, reason, path, cwd):
    '''One-line brief queued for the turn%10 / task-end flush (self-evolution feedback to the user).'''
    why = f" — {reason}" if reason else ""
    try:
        rel = os.path.relpath(path, cwd)
    except Exception:
        rel = path
    return f"\n📌[Skill蒸馏] {action} `{name}`{why} — file_read `{rel}` 查看；.prev 已备份 [approve|edit|revert]"
