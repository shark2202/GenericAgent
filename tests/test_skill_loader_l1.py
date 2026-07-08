"""
Tests for skill_loader L1 integration.

Verifies sync_skills_to_l1() correctly:
  - Scans skills and formats L1 [Skills] section
  - Project-level overrides user-level
  - Marker mechanism preserves manual notes
  - Experience file detection (dual path vs single path)
  - Edge cases: empty skills, no L1 file, unpaired markers, etc.
"""
import os
import sys
from unittest.mock import patch

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import skill_loader
from skill_loader import (
    sync_skills_to_l1,
    _replace_between_markers,
    _discover_skills,
    backup_and_patch_skill,
    SKILL_START_MARKER,
    SKILL_END_MARKER,
)


# ── Unit tests ──────────────────────────────────────────────

def test_sync_basic(tmp_path):
    """sync_skills_to_l1 correctly scans and formats L1 Skills section."""
    l1_path = str(tmp_path / "l1.txt")
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"my-skill": ("Test skill", "/path/SKILL.md")}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    assert SKILL_START_MARKER in content
    assert SKILL_END_MARKER in content
    assert "my-skill:" in content
    assert "SKILL.md" in content


def test_project_overrides_user(tmp_path):
    """Project-level skill overrides user-level same-name skill."""
    user_skills = tmp_path / "user" / ".agents" / "skills" / "shared"
    user_skills.mkdir(parents=True)
    (user_skills / "SKILL.md").write_text('---\nname: shared\ndescription: "User"\n---\n')

    proj_skills = tmp_path / "proj" / ".agents" / "skills" / "shared"
    proj_skills.mkdir(parents=True)
    (proj_skills / "SKILL.md").write_text('---\nname: shared\ndescription: "Project"\n---\n')

    with patch.dict(os.environ, {"HOME": str(tmp_path / "user")}):
        catalog = _discover_skills(cwd_skills_root=str(tmp_path / "proj"))

    assert "shared" in catalog
    assert catalog["shared"][0] == "Project"
    assert str(tmp_path / "proj") in catalog["shared"][1]


def test_marker_preserves_manual_notes(tmp_path):
    """sync updates between markers, preserves manual notes outside."""
    l1_path = str(tmp_path / "l1.txt")
    pre_content = "# Manual notes before\n"
    pre_content += f"{SKILL_START_MARKER}\nold-skill: /old/path/SKILL.md\n{SKILL_END_MARKER}\n"
    pre_content += "# Manual notes after\n"
    open(l1_path, 'w').write(pre_content)

    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"new-skill": ("Desc", "/new/path/SKILL.md")}
        sync_skills_to_l1(l1_path=l1_path)

    content = open(l1_path).read()
    assert "# Manual notes before" in content
    assert "# Manual notes after" in content
    assert "old-skill" not in content
    assert "new-skill" in content


def test_experience_file_dual_path(tmp_path):
    """Experience file exists -> dual path; absent -> single path."""
    l1_path = str(tmp_path / "l1.txt")
    skill_path = str(tmp_path / "skill" / "SKILL.md")
    os.makedirs(os.path.dirname(skill_path), exist_ok=True)
    open(skill_path, 'w').write('---\nname: test\ndescription: "T"\n---\n')

    # No experience file -> single path
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"test": ("T", skill_path)}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    assert "test: " + skill_path in content
    line = [l for l in content.split('\n') if l.startswith('test:')][0]
    assert "|" not in line

    # Create experience file -> dual path
    exp_path = os.path.join(skill_loader.script_dir, 'memory', 'skill_exp_test.md')
    os.makedirs(os.path.dirname(exp_path), exist_ok=True)
    open(exp_path, 'w').write("# Experience")
    try:
        with patch.object(skill_loader, '_discover_skills') as mock_disc:
            mock_disc.return_value = {"test": ("T", skill_path)}
            sync_skills_to_l1(l1_path=l1_path)
        content = open(l1_path).read()
        assert "|" in content
        assert "skill_exp_test.md" in content
    finally:
        os.remove(exp_path)


def test_empty_skills(tmp_path):
    """No skills -> empty marker section, no error."""
    l1_path = str(tmp_path / "l1.txt")
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    assert SKILL_START_MARKER in content
    assert SKILL_END_MARKER in content
    auto_section = content[content.index(SKILL_START_MARKER):content.index(SKILL_END_MARKER)]
    lines = [l for l in auto_section.split('\n') if l and not l.startswith('<!--')]
    assert len(lines) == 0


def test_frontmatter_no_name(tmp_path):
    """Frontmatter without name -> silently skipped."""
    skills_dir = tmp_path / ".agents" / "skills" / "no-name"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text('---\ndescription: "No name"\n---\n')
    with patch.dict(os.environ, {"HOME": str(tmp_path / "fake-home")}):
        catalog = _discover_skills(cwd_skills_root=str(tmp_path))
    assert len(catalog) == 0


def test_frontmatter_dirname_vs_name(tmp_path):
    """Directory name != frontmatter name -> use frontmatter name as key."""
    skills_dir = tmp_path / ".agents" / "skills" / "dir-name"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text('---\nname: real-name\ndescription: "X"\n---\n')
    with patch.dict(os.environ, {"HOME": str(tmp_path / "fake-home")}):
        catalog = _discover_skills(cwd_skills_root=str(tmp_path))
    assert "real-name" in catalog
    assert "dir-name" not in catalog


def test_no_frontmatter(tmp_path):
    """No frontmatter -> silently skipped."""
    skills_dir = tmp_path / ".agents" / "skills" / "plain"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text('# Just markdown, no frontmatter\n')
    with patch.dict(os.environ, {"HOME": str(tmp_path / "fake-home")}):
        catalog = _discover_skills(cwd_skills_root=str(tmp_path))
    assert len(catalog) == 0


def test_l1_file_not_exist(tmp_path):
    """L1 file does not exist -> create file with marker section."""
    l1_path = str(tmp_path / "nonexistent" / "l1.txt")
    os.makedirs(os.path.dirname(l1_path), exist_ok=True)
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"s": ("D", "/p/SKILL.md")}
        sync_skills_to_l1(l1_path=l1_path)
    assert os.path.isfile(l1_path)
    content = open(l1_path).read()
    assert SKILL_START_MARKER in content


def test_no_marker_append(tmp_path):
    """L1 without markers -> append marker section to end."""
    l1_path = str(tmp_path / "l1.txt")
    open(l1_path, 'w').write("# Existing content\nNo markers here.")
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"s": ("D", "/p/SKILL.md")}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    assert "# Existing content" in content
    assert content.index("# Existing content") < content.index(SKILL_START_MARKER)


def test_unpaired_marker(tmp_path):
    """Start marker without end -> replace from start to file end."""
    l1_path = str(tmp_path / "l1.txt")
    open(l1_path, 'w').write(f"before\n{SKILL_START_MARKER}\norphan content\n")
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"s": ("D", "/p/SKILL.md")}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    assert "before" in content
    assert "orphan content" not in content
    assert SKILL_END_MARKER in content


def test_exp_deleted_reverts(tmp_path):
    """Experience file deleted -> next sync reverts to single path."""
    l1_path = str(tmp_path / "l1.txt")
    skill_path = "/p/SKILL.md"
    exp_path = os.path.join(skill_loader.script_dir, 'memory', 'skill_exp_revert.md')
    os.makedirs(os.path.dirname(exp_path), exist_ok=True)

    open(exp_path, 'w').write("# Exp")
    with patch.object(skill_loader, '_discover_skills') as m:
        m.return_value = {"revert": ("D", skill_path)}
        sync_skills_to_l1(l1_path=l1_path)
    assert "|" in open(l1_path).read()

    os.remove(exp_path)
    with patch.object(skill_loader, '_discover_skills') as m:
        m.return_value = {"revert": ("D", skill_path)}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    line = [l for l in content.split('\n') if l.startswith('revert:')][0]
    assert "|" not in line


# ── Integration tests ───────────────────────────────────────

def test_no_catalog_in_system_prompt(monkeypatch, tmp_path):
    """get_system_prompt() output has no '## Available Skills' section."""
    monkeypatch.setattr('ga.sync_skills_to_l1', lambda *a, **k: None)
    from agentmain import get_system_prompt
    prompt = get_system_prompt()
    assert "## Available Skills" not in prompt


def test_hot_reload(tmp_path, monkeypatch):
    """New SKILL.md added -> next sync updates L1."""
    l1_path = str(tmp_path / "l1.txt")
    monkeypatch.setattr(skill_loader, 'script_dir', str(tmp_path))
    os.makedirs(os.path.join(str(tmp_path), 'memory'), exist_ok=True)

    with patch.object(skill_loader, '_discover_skills', return_value={}):
        sync_skills_to_l1(l1_path=l1_path)
    assert "new-skill" not in open(l1_path).read()

    skills_dir = tmp_path / ".agents" / "skills" / "new-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text('---\nname: new-skill\ndescription: "New"\n---\n')
    monkeypatch.setenv("HOME", str(tmp_path))

    with patch.object(skill_loader, '_discover_skills',
                      return_value={"new-skill": ("New", str(skills_dir / "SKILL.md"))}):
        sync_skills_to_l1(l1_path=l1_path)
    assert "new-skill:" in open(l1_path).read()


def test_skill_removal(tmp_path, monkeypatch):
    """Deleted SKILL.md -> L1 auto section entry disappears."""
    l1_path = str(tmp_path / "l1.txt")
    skill_dir = tmp_path / ".agents" / "skills" / "temp-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text('---\nname: temp-skill\ndescription: "T"\n---\n')
    monkeypatch.setenv("HOME", str(tmp_path))

    with patch.object(skill_loader, '_discover_skills',
                      return_value={"temp-skill": ("T", str(skill_dir / "SKILL.md"))}):
        sync_skills_to_l1(l1_path=l1_path)
    assert "temp-skill:" in open(l1_path).read()

    import shutil
    shutil.rmtree(skill_dir)
    with patch.object(skill_loader, '_discover_skills', return_value={}):
        sync_skills_to_l1(l1_path=l1_path)
    assert "temp-skill:" not in open(l1_path).read()


# ── Token budget test ───────────────────────────────────────

def test_token_budget(tmp_path):
    """L1 Skills section line count <= skill count + 2 markers, no description leakage."""
    l1_path = str(tmp_path / "l1.txt")
    catalog = {}
    for i in range(17):
        catalog[f"skill-{i:02d}"] = ("x" * 500, f"/path/skill-{i:02d}/SKILL.md")

    with patch.object(skill_loader, '_discover_skills', return_value=catalog):
        sync_skills_to_l1(l1_path=l1_path)

    content = open(l1_path).read()
    auto_section = content[content.index(SKILL_START_MARKER):content.index(SKILL_END_MARKER) + len(SKILL_END_MARKER)]
    line_count = len([l for l in auto_section.strip().split('\n') if l])
    assert line_count == 19  # 17 entries + 2 markers
    assert "x" * 100 not in content  # no description leakage


# ── Optional backup tests ───────────────────────────────────

def test_backup_skill_disabled():
    """Without GA_SKILL_PATCH_ENABLED -> raise PermissionError."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(PermissionError):
            backup_and_patch_skill("/fake/SKILL.md", "patch")


def test_backup_skill_creates_bak(tmp_path):
    """With env var set -> create .bak file (first time only)."""
    skill = tmp_path / "SKILL.md"
    skill.write_text("original")
    with patch.dict(os.environ, {"GA_SKILL_PATCH_ENABLED": "1"}):
        backup_and_patch_skill(str(skill), "patch")
        assert (tmp_path / "SKILL.md.bak").exists()
        assert (tmp_path / "SKILL.md.bak").read_text() == "original"
        # Second call does not overwrite existing backup
        skill.write_text("modified")
        backup_and_patch_skill(str(skill), "patch")
        assert (tmp_path / "SKILL.md.bak").read_text() == "original"
