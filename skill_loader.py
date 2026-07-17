"""Skill loader: discover skills and sync to L1 memory index."""
import difflib
import os
import re
import shutil

SKILL_FILE = "SKILL.md"
script_dir = os.path.dirname(os.path.abspath(__file__))

SKILL_START_MARKER = '<!-- auto-skills-start -->'
SKILL_END_MARKER = '<!-- auto-skills-end -->'


def _parse_skill_frontmatter(skill_md_path):
    """Parse YAML frontmatter from a SKILL.md file. Returns (name, description) or (None, None)."""
    try:
        with open(skill_md_path, 'r', encoding='utf-8') as f:
            content = f.read(2048)
    except Exception:
        return None, None

    if not content.startswith('---'):
        return None, None

    end = content.find('---', 3)
    if end == -1:
        return None, None

    fm = content[3:end]

    name_match = re.search(r'^name:\s*(.+)', fm, re.MULTILINE)
    if not name_match:
        return None, None
    name = name_match.group(1).strip().strip('"').strip("'")

    desc_match = re.search(r'^description:\s*["\']?(.+?)["\']?\s*$', fm, re.MULTILINE)
    description = desc_match.group(1).strip() if desc_match else ""

    return name, description


def _scan_dir(skills_root):
    """Scan a skills root directory. Returns {name: (description, path)} dict."""
    catalog = {}
    if not os.path.isdir(skills_root):
        return catalog
    for entry in sorted(os.listdir(skills_root)):
        skill_dir = os.path.join(skills_root, entry)
        if not os.path.isdir(skill_dir):
            continue
        md_path = os.path.join(skill_dir, SKILL_FILE)
        if not os.path.isfile(md_path):
            continue
        name, desc = _parse_skill_frontmatter(md_path)
        if name:
            catalog[name] = (desc, os.path.abspath(md_path))
    return catalog


def _skill_roots(cwd_skills_root=None):
    """Return list of existing skills root dirs (user-level then project-level)."""
    home = os.environ.get('HOME') or os.environ.get('USERPROFILE') or os.path.expanduser('~')
    user_root = os.path.join(home, '.agents', 'skills') if home else None
    cwd = cwd_skills_root or os.getcwd()
    project_root = os.path.join(cwd, '.agents', 'skills')
    roots = []
    if user_root and os.path.isdir(user_root):
        roots.append(user_root)
    if os.path.isdir(project_root):
        roots.append(project_root)
    return roots


def _discover_skills(cwd_skills_root=None):
    """Discover all skills: user-level + project-level (project overrides same-name).

    Args:
        cwd_skills_root: Override CWD for testing. Defaults to os.getcwd().

    Returns:
        {name: (description, abs_skill_md_path)} dict, possibly empty.
    """
    catalog = {}
    for root in _skill_roots(cwd_skills_root):
        catalog.update(_scan_dir(root))
    return catalog


_SKILL_CATALOG_CACHE = None
_SKILL_MTIME_CACHE = None


def _compute_skill_mtimes(catalog):
    """Snapshot mtimes for skill roots and each SKILL.md in catalog."""
    root_mtimes = {}
    for r in _skill_roots():
        try:
            root_mtimes[r] = os.stat(r).st_mtime
        except OSError:
            pass
    file_mtimes = {}
    for _name, (_desc, path) in catalog.items():
        try:
            file_mtimes[path] = os.stat(path).st_mtime
        except OSError:
            pass
    return {'roots': root_mtimes, 'files': file_mtimes}


def _check_skill_mtimes(cached):
    """Return True if cached root set + all root/file mtimes unchanged."""
    try:
        current_roots = set(_skill_roots())
    except Exception:
        return False
    if current_roots != set(cached['roots'].keys()):
        return False
    for r, mtime in cached['roots'].items():
        try:
            if os.stat(r).st_mtime != mtime:
                return False
        except OSError:
            return False
    for path, mtime in cached['files'].items():
        try:
            if os.stat(path).st_mtime != mtime:
                return False
        except OSError:
            return False
    return True


def _get_skills_catalog():
    """Return (catalog, fresh). Uses mtime cache to skip rescan when unchanged.

    fresh=True  -> cached catalog reused (no rescan, no IO rewrite needed).
    fresh=False -> catalog just rescanned (cold start or detected changes).
    """
    global _SKILL_CATALOG_CACHE, _SKILL_MTIME_CACHE

    if _SKILL_CATALOG_CACHE is None or _SKILL_MTIME_CACHE is None:
        catalog = _discover_skills()
        _SKILL_CATALOG_CACHE = catalog
        _SKILL_MTIME_CACHE = _compute_skill_mtimes(catalog)
        return catalog, False

    if _check_skill_mtimes(_SKILL_MTIME_CACHE):
        return _SKILL_CATALOG_CACHE, True

    catalog = _discover_skills()
    _SKILL_CATALOG_CACHE = catalog
    _SKILL_MTIME_CACHE = _compute_skill_mtimes(catalog)
    return catalog, False


def _reset_skill_cache():
    """Clear the mtime/catalog cache (for tests or forced refresh)."""
    global _SKILL_CATALOG_CACHE, _SKILL_MTIME_CACHE
    _SKILL_CATALOG_CACHE = None
    _SKILL_MTIME_CACHE = None


def _parse_full_frontmatter(skill_md_path):
    """Parse all frontmatter fields per agentskills.io spec.

    Returns dict with any of: name, description, license, compatibility,
    metadata, allowed_tools. Missing/absent fields are omitted.
    """
    try:
        with open(skill_md_path, encoding='utf-8') as f:
            content = f.read(4096)
    except Exception:
        return {}
    if not content.startswith('---'):
        return {}
    end = content.find('---', 3)
    if end == -1:
        return {}
    fm = content[3:end]
    result = {}

    def _field(key, fm_text):
        m = re.search(r'^' + re.escape(key) + r':\s*(.+)', fm_text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")
        return None

    name = _field('name', fm)
    if name:
        result['name'] = name
    desc = _field('description', fm)
    if desc:
        result['description'] = desc
    license_ = _field('license', fm)
    if license_:
        result['license'] = license_
    compat = _field('compatibility', fm)
    if compat:
        result['compatibility'] = compat
    allowed = _field('allowed-tools', fm)
    if allowed:
        result['allowed_tools'] = allowed
    meta = _field('metadata', fm)
    if meta:
        result['metadata'] = meta
    return result


def get_skill_detail(name):
    """Lazy-load a single skill's detail (progressive disclosure).

    Per agentskills.io spec: returns Metadata-layer fields (name, description,
    plus optional license/compatibility/metadata/allowed_tools), a Resources
    overview (has_scripts/has_references/has_assets), and the Instructions
    entry point (skill_md_path). Use file_read on skill_md_path for full body.

    Args:
        name: Skill name (frontmatter name key).

    Returns:
        dict. On unknown name: {'status': 'error', 'msg': ..., 'available': [...]}.
    """
    catalog, _ = _get_skills_catalog()
    if name not in catalog:
        return {
            'status': 'error',
            'msg': f"Skill '{name}' not found.",
            'available': sorted(catalog.keys()),
        }
    desc, skill_md_path = catalog[name]
    detail = {'name': name, 'description': desc, 'skill_md_path': skill_md_path}
    fm = _parse_full_frontmatter(skill_md_path)
    for k in ('license', 'compatibility', 'metadata', 'allowed_tools'):
        if k in fm:
            detail[k] = fm[k]
    skill_dir = os.path.dirname(skill_md_path)
    detail['has_scripts'] = os.path.isdir(os.path.join(skill_dir, 'scripts'))
    detail['has_references'] = os.path.isdir(os.path.join(skill_dir, 'references'))
    detail['has_assets'] = os.path.isdir(os.path.join(skill_dir, 'assets'))
    return detail


def _replace_between_markers(filepath, start_marker, end_marker, new_content):
    """Replace content between start_marker and end_marker, preserving everything outside.

    Boundary conditions:
    - File does not exist -> create empty file then write new_content
    - Markers not found -> append to file end
    - Only start marker, no end -> replace from start to file end
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        content = ""

    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)

    if start_idx == -1:
        if content and not content.endswith('\n'):
            content += '\n'
        content += new_content + '\n'
    else:
        if end_idx == -1:
            end_idx = len(content)
        else:
            end_idx += len(end_marker)
        content = content[:start_idx] + new_content + content[end_idx:]

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)


def sync_skills_to_l1(l1_path=None):
    """Scan skill directories -> sync to L1 [Skills] section (between auto markers).

    Hot/cold separation:
      - Hot (has experience file): full path + experience path, one line per skill
      - Cold (no experience file): comma-separated names, one line for all

    Preserves manual notes outside markers.

    Args:
        l1_path: L1 file path override (for testing). Defaults to <script_dir>/memory/global_mem_insight.txt
    """
    l1_path = l1_path or os.path.join(script_dir, 'memory', 'global_mem_insight.txt')

    # mtime cache skips expensive _discover_skills rescan when unchanged.
    # L1 rewrite always runs: hot/cold classification depends on experience
    # files (memory/skill_exp_*.md) which are NOT tracked by the mtime cache.
    catalog, _fresh = _get_skills_catalog()

    hot_entries = []
    cold_names = []
    for name, (desc, skill_path) in sorted(catalog.items()):
        exp_path = os.path.join(script_dir, 'memory', f'skill_exp_{name}.md')
        if os.path.isfile(exp_path):
            hot_entries.append(f"{name}: {skill_path} | {exp_path}")
        else:
            cold_names.append(name)

    auto_block = f"{SKILL_START_MARKER}\n"
    if hot_entries:
        auto_block += "\n".join(hot_entries) + "\n"
    if cold_names:
        auto_block += ", ".join(cold_names) + "\n"
    auto_block += SKILL_END_MARKER

    _replace_between_markers(l1_path, SKILL_START_MARKER, SKILL_END_MARKER, auto_block)


def backup_and_patch_skill(skill_md_path, patch_content=None):
    """opt-in: backup SKILL.md original before Agent patches it.

    Requires GA_SKILL_PATCH_ENABLED=1 environment variable.
    This function only handles backup; actual patch is done by Agent's file_patch tool.

    Args:
        skill_md_path: Absolute path to SKILL.md
        patch_content: Expected patch content (currently unused, reserved for future validation)

    Raises:
        PermissionError: Environment variable not set
    """
    if os.environ.get('GA_SKILL_PATCH_ENABLED') != '1':
        raise PermissionError("Skill patch requires GA_SKILL_PATCH_ENABLED=1")

    bak_path = skill_md_path + '.bak'
    if not os.path.exists(bak_path):
        shutil.copy2(skill_md_path, bak_path)


# ── Self-evolution: provenance / validation / retrieval / revert ───────────
# These back the post-task skill-distillation loop (see
# hermes/workflows/post-task-skill-distillation.md). Pure helpers, no global
# state mutation; the distillation plugin orchestrates them.

def _read_frontmatter_block(skill_md_path, limit=4096):
    """Return the raw frontmatter text (between the --- fences), or None."""
    try:
        with open(skill_md_path, encoding='utf-8') as f:
            content = f.read(limit)
    except Exception:
        return None
    if not content.startswith('---'):
        return None
    end = content.find('---', 3)
    if end == -1:
        return None
    return content[3:end]


def _fm_field(key, fm):
    """Read a single frontmatter field value (stripped/quoted), or None."""
    m = re.search(r'^' + re.escape(key) + r':\s*(.+)', fm, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else None


def _parse_bool(s, default=False):
    if s is None:
        return default
    return s.strip().lower() in ('true', '1', 'yes', 'on')


def _parse_inline_list(s):
    """Parse a YAML-ish inline list '[a, b]' or a bare comma list into [str, ...]."""
    if not s:
        return []
    s = s.strip()
    if s.startswith('[') and s.endswith(']'):
        s = s[1:-1]
    if not s.strip():
        return []
    return [x.strip().strip('"').strip("'") for x in s.split(',') if x.strip()]


def parse_provenance(skill_md_path):
    """Read self-evolution provenance fields from a SKILL.md frontmatter.

    Returns dict: author, evolvable, evolved_from, version, created_at,
    mcp_dependencies, fitness. Defaults: author='user'; evolvable derives
    from author (True iff author=='agent') when unset; lists default to [].
    A file with no frontmatter returns defaults with evolvable=False.
    """
    fm = _read_frontmatter_block(skill_md_path)
    if fm is None:
        return {'author': 'user', 'evolvable': False, 'evolved_from': None,
                'version': None, 'created_at': None, 'mcp_dependencies': [], 'fitness': None}
    author = _fm_field('author', fm) or 'user'
    evolvable = _parse_bool(_fm_field('evolvable', fm), default=(author == 'agent'))
    return {
        'author': author,
        'evolvable': evolvable,
        'evolved_from': _fm_field('evolved_from', fm),
        'version': _fm_field('version', fm),
        'created_at': _fm_field('created_at', fm),
        'mcp_dependencies': _parse_inline_list(_fm_field('mcp_dependencies', fm)),
        'fitness': _fm_field('fitness', fm),
    }


def validate_skill(skill_md_path):
    """Structural validation for a candidate SKILL.md.

    Returns (ok, reason). ok=True iff frontmatter has name+description,
    description length <= 120, and the body has >=1 '## ' heading. Purely
    structural; does not depend on the catalog cache, so it works on a
    freshly-written skill before it is discovered/indexed.
    """
    name, desc = _parse_skill_frontmatter(skill_md_path)
    if not name:
        return False, "frontmatter missing name"
    if not desc:
        return False, "frontmatter missing description"
    if len(desc) > 120:
        return False, f"description too long ({len(desc)} > 120)"
    try:
        with open(skill_md_path, encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return False, f"read error: {e}"
    body = content
    if content.startswith('---'):
        end = content.find('---', 3)
        if end != -1:
            body = content[end + 3:]
    if not re.search(r'^##\s', body, re.MULTILINE):
        return False, "body has no '## ' heading"
    return True, ""


def retrieve_skill_by_desc(desc, catalog=None):
    """Find the existing skill whose name+description best matches `desc`.

    v1: difflib SequenceMatcher over lowercased "name description".
    Returns the best-matching skill name if ratio > 0.5, else None. Pass an
    explicit `catalog` ({name: (description, path)}) to bypass the global cache.
    """
    if not desc:
        return None
    if catalog is None:
        catalog, _ = _get_skills_catalog()
    target = desc.lower()
    best_name, best_ratio = None, 0.0
    for name, (skill_desc, _path) in catalog.items():
        candidate = f"{name} {skill_desc}".lower()
        ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
        if ratio > best_ratio:
            best_name, best_ratio = name, ratio
    return best_name if best_ratio > 0.5 else None


def backup_skill_prev(skill_md_path):
    """Snapshot current SKILL.md to `<path>.prev` (overwrites prior snapshot).

    Used by the self-evolution loop before an autonomous patch, enabling
    one-step revert. Pure backup (no SKILL.md mutation); the write itself is
    gated elsewhere (do_skill_manage / GA_SKILL_EVOLUTION_ENABLED).
    Returns the .prev path on success, None if source missing.
    """
    if not os.path.isfile(skill_md_path):
        return None
    prev = skill_md_path + '.prev'
    shutil.copy2(skill_md_path, prev)
    return prev


def revert_skill_prev(skill_md_path):
    """Restore SKILL.md from its `.prev` snapshot. Returns True on revert."""
    prev = skill_md_path + '.prev'
    if not os.path.isfile(prev):
        return False
    shutil.copy2(prev, skill_md_path)
    return True
