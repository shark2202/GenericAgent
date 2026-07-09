"""Skill loader: discover skills and sync to L1 memory index."""
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


def _discover_skills(cwd_skills_root=None):
    """Discover all skills: user-level + project-level (project overrides same-name).

    Args:
        cwd_skills_root: Override CWD for testing. Defaults to os.getcwd().

    Returns:
        {name: (description, abs_skill_md_path)} dict, possibly empty.
    """
    home = os.environ.get('HOME') or os.environ.get('USERPROFILE') or os.path.expanduser('~')
    user_root = os.path.join(home, '.agents', 'skills') if home else None
    cwd = cwd_skills_root or os.getcwd()
    project_root = os.path.join(cwd, '.agents', 'skills')

    catalog = {}
    if user_root:
        catalog.update(_scan_dir(user_root))
    catalog.update(_scan_dir(project_root))
    return catalog


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

    catalog = _discover_skills()

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
