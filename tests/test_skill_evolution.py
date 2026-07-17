"""
Tests for skill_loader self-evolution helpers (post-task skill-distillation, L1).

Covers the skill_loader-side units of the v1 self-evolution loop:
  - parse_provenance: frontmatter author/evolvable/evolved_from/mcp_dependencies
  - validate_skill: structural gate (name+desc, desc<=120, >=1 '## ' heading)
  - retrieve_skill_by_desc: difflib match against the skill catalog
  - backup_skill_prev / revert_skill_prev: one-step revert snapshot

Hook/trigger/distill behaviour (T1,T2,T3,T8,T9,T10) is exercised by the
plugins/skill_evolution integration tests (added in a later step).
"""

import os
import sys
from unittest.mock import patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from skill_loader import (  # noqa: E402
    backup_skill_prev,
    parse_provenance,
    retrieve_skill_by_desc,
    revert_skill_prev,
    validate_skill,
)


def _write_skill(path, frontmatter_extra="", body="# Body\n## When to Use\n"):
    """Write a minimal valid SKILL.md with optional extra frontmatter fields."""
    fm = '---\nname: my-skill\ndescription: "Does useful stuff"\n'
    fm += frontmatter_extra
    fm += "---\n"
    path.write_text(fm + body, encoding="utf-8")
    return str(path)


# ── parse_provenance ──────────────────────────────────────────────


def test_parse_provenance_defaults(tmp_path):
    """Skill with no provenance fields -> author=user, evolvable=False."""
    p = tmp_path / "SKILL.md"
    _write_skill(p)
    prov = parse_provenance(str(p))
    assert prov["author"] == "user"
    assert prov["evolvable"] is False
    assert prov["evolved_from"] is None
    assert prov["mcp_dependencies"] == []
    assert prov["fitness"] is None


def test_parse_provenance_agent_implies_evolvable(tmp_path):
    """author=agent with no evolvable field -> evolvable=True."""
    p = tmp_path / "SKILL.md"
    _write_skill(p, frontmatter_extra="author: agent\nversion: 0.1\n")
    prov = parse_provenance(str(p))
    assert prov["author"] == "agent"
    assert prov["evolvable"] is True
    assert prov["version"] == "0.1"


def test_parse_provenance_evolvable_explicit_false(tmp_path):
    """author=agent but evolvable:false -> evolvable False (opt-out)."""
    p = tmp_path / "SKILL.md"
    _write_skill(p, frontmatter_extra="author: agent\nevolvable: false\n")
    prov = parse_provenance(str(p))
    assert prov["author"] == "agent"
    assert prov["evolvable"] is False


def test_parse_provenance_mcp_list(tmp_path):
    """mcp_dependencies inline list parses to a list of strings."""
    p = tmp_path / "SKILL.md"
    _write_skill(
        p,
        frontmatter_extra='author: agent\nmcp_dependencies: ["github/issues", "filesystem/read"]\nevolved_from: root-skill\n',
    )
    prov = parse_provenance(str(p))
    assert prov["mcp_dependencies"] == ["github/issues", "filesystem/read"]
    assert prov["evolved_from"] == "root-skill"


def test_parse_provenance_no_frontmatter(tmp_path):
    """Plain markdown (no frontmatter) -> defaults, evolvable=False."""
    p = tmp_path / "SKILL.md"
    p.write_text("# Just a title\n## Section\n", encoding="utf-8")
    prov = parse_provenance(str(p))
    assert prov["author"] == "user"
    assert prov["evolvable"] is False


# ── validate_skill ────────────────────────────────────────────────


def test_validate_skill_ok(tmp_path):
    p = tmp_path / "SKILL.md"
    _write_skill(p)
    ok, reason = validate_skill(str(p))
    assert ok is True
    assert reason == ""


def test_validate_skill_missing_name(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text('---\ndescription: "no name"\n---\n## H\n', encoding="utf-8")
    ok, reason = validate_skill(str(p))
    assert ok is False
    assert "name" in reason


def test_validate_skill_desc_too_long(tmp_path):
    p = tmp_path / "SKILL.md"
    long_desc = "x" * 200
    p.write_text(f'---\nname: big\ndescription: "{long_desc}"\n---\n## H\n', encoding="utf-8")
    ok, reason = validate_skill(str(p))
    assert ok is False
    assert "too long" in reason


def test_validate_skill_no_heading(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text('---\nname: flat\ndescription: "ok"\n---\nJust prose, no headings.\n', encoding="utf-8")
    ok, reason = validate_skill(str(p))
    assert ok is False
    assert "heading" in reason


def test_validate_skill_missing_file(tmp_path):
    ok, reason = validate_skill(str(tmp_path / "nope.md"))
    assert ok is False
    assert "name" in reason  # _parse_skill_frontmatter returns (None,None) on missing file


# ── retrieve_skill_by_desc ───────────────────────────────────────


def test_retrieve_matches_existing(tmp_path):
    catalog = {
        "github-pr": ("Open and review GitHub pull requests", "/p/github-pr/SKILL.md"),
        "email-triage": ("Triage inbox emails by priority", "/p/email/SKILL.md"),
    }
    assert retrieve_skill_by_desc("review a github pull request", catalog=catalog) == "github-pr"


def test_retrieve_no_match_below_threshold(tmp_path):
    catalog = {"github-pr": ("Open GitHub PRs", "/p/SKILL.md")}
    assert retrieve_skill_by_desc("completely unrelated cooking recipe", catalog=catalog) is None


def test_retrieve_empty_desc():
    assert retrieve_skill_by_desc("", catalog={}) is None
    assert retrieve_skill_by_desc(None, catalog={}) is None


# ── backup_skill_prev / revert_skill_prev ─────────────────────────


def test_backup_prev_then_revert(tmp_path):
    p = tmp_path / "SKILL.md"
    _write_skill(p, body="# V1\n## H\noriginal content\n")
    path = str(p)

    backup_skill_prev(path)  # snapshot original
    assert os.path.isfile(path + ".prev")

    # Mutate the skill (simulating an autonomous patch)
    p.write_text('---\nname: my-skill\ndescription: "mutated"\n---\n# V2\n## H\nchanged\n', encoding="utf-8")
    assert "mutated" in p.read_text(encoding="utf-8")

    # Revert restores the snapshot
    assert revert_skill_prev(path) is True
    restored = p.read_text(encoding="utf-8")
    assert "original content" in restored
    assert "mutated" not in restored


def test_backup_prev_missing_source(tmp_path):
    assert backup_skill_prev(str(tmp_path / "absent.md")) is None


def test_revert_without_prev(tmp_path):
    p = tmp_path / "SKILL.md"
    _write_skill(p)
    assert revert_skill_prev(str(p)) is False


# ── does not disturb existing .bak mechanism ───────────────────────


def test_prev_independent_of_bak(tmp_path):
    """backup_skill_prev (.prev) is separate from backup_and_patch_skill (.bak)."""
    from skill_loader import backup_and_patch_skill

    p = tmp_path / "SKILL.md"
    _write_skill(p, body="# O\n## H\n")
    path = str(p)

    with patch.dict(os.environ, {"GA_SKILL_PATCH_ENABLED": "1"}):
        backup_and_patch_skill(path)  # creates .bak
    backup_skill_prev(path)  # creates .prev
    assert os.path.isfile(path + ".bak")
    assert os.path.isfile(path + ".prev")
