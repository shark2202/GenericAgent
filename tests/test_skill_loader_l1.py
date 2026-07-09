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
    get_skill_detail,
    _get_skills_catalog,
    _reset_skill_cache,
    SKILL_START_MARKER,
    SKILL_END_MARKER,
)


@pytest.fixture(autouse=True)
def _reset_skill_cache_fixture():
    """Reset mtime/catalog cache before and after each test (isolation)."""
    _reset_skill_cache()
    yield
    _reset_skill_cache()


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
    assert "my-skill" in content  # cold skill: name in comma list


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
    """Hot skill (has exp file) -> full path + exp; Cold skill (no exp) -> name in comma list."""
    l1_path = str(tmp_path / "l1.txt")
    skill_path = str(tmp_path / "skill" / "SKILL.md")
    os.makedirs(os.path.dirname(skill_path), exist_ok=True)
    open(skill_path, 'w').write('---\nname: test\ndescription: "T"\n---\n')

    # No experience file -> cold -> name in comma list, no full path
    with patch.object(skill_loader, '_discover_skills') as mock_disc:
        mock_disc.return_value = {"test": ("T", skill_path)}
        sync_skills_to_l1(l1_path=l1_path)
    content = open(l1_path).read()
    assert "test" in content
    assert "test:" not in content  # cold: no "name:" format
    assert skill_path not in content  # cold: no full path

    # Create experience file -> hot -> full path + exp path
    exp_path = os.path.join(skill_loader.script_dir, 'memory', 'skill_exp_test.md')
    os.makedirs(os.path.dirname(exp_path), exist_ok=True)
    open(exp_path, 'w').write("# Experience")
    try:
        with patch.object(skill_loader, '_discover_skills') as mock_disc:
            mock_disc.return_value = {"test": ("T", skill_path)}
            sync_skills_to_l1(l1_path=l1_path)
        content = open(l1_path).read()
        assert "test:" in content  # hot: "name:" format
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
    assert "revert:" not in content  # cold: no "name:" format
    assert "revert" in content  # name in comma list


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
    assert "new-skill" in open(l1_path).read()  # cold: name in comma list


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
    assert "temp-skill" in open(l1_path).read()  # cold: name in comma list

    import shutil
    shutil.rmtree(skill_dir)
    with patch.object(skill_loader, '_discover_skills', return_value={}):
        sync_skills_to_l1(l1_path=l1_path)
    assert "temp-skill" not in open(l1_path).read()


# ── Token budget test ───────────────────────────────────────

def test_token_budget(tmp_path):
    """L1 Skills section: cold skills = 1 comma line, no description leakage."""
    l1_path = str(tmp_path / "l1.txt")
    catalog = {}
    for i in range(17):
        catalog[f"skill-{i:02d}"] = ("x" * 500, f"/path/skill-{i:02d}/SKILL.md")

    with patch.object(skill_loader, '_discover_skills', return_value=catalog):
        sync_skills_to_l1(l1_path=l1_path)

    content = open(l1_path).read()
    auto_section = content[content.index(SKILL_START_MARKER):content.index(SKILL_END_MARKER) + len(SKILL_END_MARKER)]
    line_count = len([l for l in auto_section.strip().split('\n') if l])
    assert line_count == 3  # 1 comma-separated cold line + 2 markers
    assert "x" * 100 not in content  # no description leakage


def test_hot_cold_separation(tmp_path, monkeypatch):
    """Hot skills (exp file) get full path; cold skills get comma list."""
    l1_path = str(tmp_path / "l1.txt")
    monkeypatch.setattr(skill_loader, 'script_dir', str(tmp_path))
    os.makedirs(os.path.join(str(tmp_path), 'memory'), exist_ok=True)

    # Create experience file for hot-skill only
    exp_path = os.path.join(str(tmp_path), 'memory', 'skill_exp_hot.md')
    open(exp_path, 'w').write("# Exp")

    catalog = {
        "hot": ("Hot desc", "/path/hot/SKILL.md"),
        "cold-a": ("Cold A", "/path/cold-a/SKILL.md"),
        "cold-b": ("Cold B", "/path/cold-b/SKILL.md"),
    }
    with patch.object(skill_loader, '_discover_skills', return_value=catalog):
        sync_skills_to_l1(l1_path=l1_path)

    content = open(l1_path).read()
    # Hot: full path + exp, with | separator
    assert "hot:" in content
    assert "/path/hot/SKILL.md" in content
    assert "skill_exp_hot.md" in content
    assert "|" in content
    # Cold: comma-separated names, no full paths
    assert "cold-a" in content
    assert "cold-b" in content
    assert "," in content  # comma separator between cold names
    assert "/path/cold-a/SKILL.md" not in content
    assert "/path/cold-b/SKILL.md" not in content


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


# ── Windows path resolution tests ──────────────────────────────

def test_discover_skills_home_unset_uses_userprofile(tmp_path, monkeypatch):
    """HOME unset (Windows) + USERPROFILE set -> discover skills via USERPROFILE.

    Regression: pre-fix, HOME='' caused user_root=None, so user-level skills
    were never scanned on Windows. This test does NOT mock _discover_skills.
    """
    user_home = tmp_path / "winhome"
    skill_dir = user_home / ".agents" / "skills" / "win-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text('---\nname: win-skill\ndescription: "W"\n---\n')

    proj = tmp_path / "proj"
    proj.mkdir()

    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", str(user_home))

    catalog = _discover_skills(cwd_skills_root=str(proj))
    assert "win-skill" in catalog
    assert str(skill_dir / "SKILL.md") == catalog["win-skill"][1]


# ── get_skill_detail tests (progressive disclosure) ─────────────

def test_get_skill_detail_returns_summary(tmp_path, monkeypatch):
    """get_skill_detail returns frontmatter + resource overview + path."""
    skills_root = tmp_path / "home" / ".agents" / "skills"
    skill_dir = skills_root / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nname: my-skill\ndescription: "Does stuff"\nlicense: MIT\n---\n# Body\n'
    )
    (skill_dir / "scripts").mkdir()
    monkeypatch.setattr(skill_loader, '_skill_roots', lambda *a, **k: [str(skills_root)])

    detail = get_skill_detail("my-skill")
    assert detail['name'] == "my-skill"
    assert detail['description'] == "Does stuff"
    assert detail['license'] == "MIT"
    assert detail['skill_md_path'].endswith("SKILL.md")
    assert detail['has_scripts'] is True
    assert detail['has_references'] is False
    assert detail['has_assets'] is False


def test_get_skill_detail_not_found(tmp_path, monkeypatch):
    """Unknown skill name -> error dict with available list."""
    skills_root = tmp_path / "home" / ".agents" / "skills"
    skill_dir = skills_root / "real"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text('---\nname: real\ndescription: "R"\n---\n')
    monkeypatch.setattr(skill_loader, '_skill_roots', lambda *a, **k: [str(skills_root)])

    detail = get_skill_detail("nope")
    assert detail['status'] == 'error'
    assert 'not found' in detail['msg']
    assert detail['available'] == ["real"]


def test_get_skill_detail_missing_frontmatter(tmp_path, monkeypatch):
    """SKILL.md without frontmatter -> defaults, no crash."""
    skills_root = tmp_path / "home" / ".agents" / "skills"
    skill_dir = skills_root / "plain"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text('# just markdown\n')
    monkeypatch.setattr(skill_loader, '_skill_roots', lambda *a, **k: [str(skills_root)])

    # _discover_skills skips no-frontmatter skills, so this skill won't be in catalog
    detail = get_skill_detail("plain")
    assert detail['status'] == 'error'


# ── mtime cache tests ──────────────────────────────────────────

def test_mtime_cache_skip_on_no_change(tmp_path, monkeypatch):
    """Second sync with no changes -> cache hit, _discover_skills not recalled."""
    skills_root = tmp_path / "home" / ".agents" / "skills"
    skill_dir = skills_root / "cached-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text('---\nname: cached-skill\ndescription: "C"\n---\n')
    monkeypatch.setattr(skill_loader, '_skill_roots', lambda *a, **k: [str(skills_root)])
    l1_path = str(tmp_path / "l1.txt")

    sync_skills_to_l1(l1_path=l1_path)
    first_content = open(l1_path).read()
    assert "cached-skill" in first_content

    # Second call: no changes -> fresh, should skip rewrite
    call_count = [0]
    original = skill_loader._discover_skills

    def counting_discover(*a, **kw):
        call_count[0] += 1
        return original(*a, **kw)

    monkeypatch.setattr(skill_loader, '_discover_skills', counting_discover)
    sync_skills_to_l1(l1_path=l1_path)
    assert call_count[0] == 0  # cache hit, no rescan


def test_mtime_cache_refresh_on_content_change(tmp_path, monkeypatch):
    """SKILL.md content edited -> mtime changes -> rescan + rewrite."""
    skills_root = tmp_path / "home" / ".agents" / "skills"
    skill_dir = skills_root / "edit-skill"
    skill_dir.mkdir(parents=True)
    md = skill_dir / "SKILL.md"
    md.write_text('---\nname: edit-skill\ndescription: "v1"\n---\n')
    monkeypatch.setattr(skill_loader, '_skill_roots', lambda *a, **k: [str(skills_root)])
    l1_path = str(tmp_path / "l1.txt")

    sync_skills_to_l1(l1_path=l1_path)
    assert "edit-skill" in open(l1_path).read()

    # Edit content (description change) — but L1 cold only stores names, so
    # verify via get_skill_detail that cache refreshes and picks up new desc
    import time
    md.write_text('---\nname: edit-skill\ndescription: "v2"\n---\n')
    time.sleep(0.05)  # ensure mtime tick on coarse-resolution filesystems

    detail = get_skill_detail("edit-skill")
    assert detail['description'] == "v2"


def test_mtime_cache_detects_add_remove(tmp_path, monkeypatch):
    """Adding/removing a skill dir -> root mtime changes -> rescan."""
    skills_root = tmp_path / "home" / ".agents" / "skills"
    skills_root.mkdir(parents=True)
    skill_dir = skills_root / "first"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text('---\nname: first\ndescription: "F"\n---\n')
    monkeypatch.setattr(skill_loader, '_skill_roots', lambda *a, **k: [str(skills_root)])

    catalog, _ = _get_skills_catalog()
    assert "first" in catalog

    # Add a new skill
    import time
    new_dir = skills_root / "second"
    new_dir.mkdir()
    (new_dir / "SKILL.md").write_text('---\nname: second\ndescription: "S"\n---\n')
    time.sleep(0.05)

    catalog2, fresh2 = _get_skills_catalog()
    assert fresh2 is False
    assert "second" in catalog2

    # No further changes -> fresh
    catalog3, fresh3 = _get_skills_catalog()
    assert fresh3 is True
