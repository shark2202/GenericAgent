"""Skill loader: discover and catalog AGENT SKILLs from .agents/skills/ directories."""
import os
import re

SKILL_FILE = "SKILL.md"


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


def get_skill_catalog(cwd_skills_root=None):
    """Build a skill catalog string for system prompt injection.

    Scans $HOME/.agents/skills/ (user-level) and $CWD/.agents/skills/ (project-level).
    Project-level skills override user-level skills with the same name.

    Args:
        cwd_skills_root: Override CWD for testing. Defaults to os.getcwd().

    Returns:
        Formatted catalog string, or empty string if no skills found.
    """
    home = os.environ.get('HOME', '')
    user_root = os.path.join(home, '.agents', 'skills') if home else None
    cwd = cwd_skills_root or os.getcwd()
    project_root = os.path.join(cwd, '.agents', 'skills')

    catalog = {}

    if user_root:
        catalog.update(_scan_dir(user_root))

    catalog.update(_scan_dir(project_root))

    if not catalog:
        return ""

    lines = ["## Available Skills", ""]
    for name, (desc, path) in sorted(catalog.items()):
        lines.append(f"- **{name}**: {desc} ({path})")

    return "\n".join(lines) + "\n"
