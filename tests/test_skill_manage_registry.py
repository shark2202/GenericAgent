"""Verifies do_skill_manage dispatches via the registry-track after migration
(the method is removed from GenericAgentHandler; it lives in tools/skill_manage.py)."""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import agent_loop  # noqa: E402


def test_skill_manage_registered_after_discover():
    """discover_tools(tools/) loads tools.skill_manage, which self-registers."""
    tools_dir = os.path.join(_REPO_ROOT, "tools")
    assert os.path.isdir(tools_dir), "tools/ package dir must exist"
    # Ensure the module is (re)imported so the decorator runs even if a prior
    # test imported it under a different state.
    sys.modules.pop("tools.skill_manage", None)
    agent_loop.discover_tools(tools_dir)
    assert agent_loop.get_tool("skill_manage") is not None


def test_skill_manage_not_a_method_on_handler():
    """The method MUST be gone from GenericAgentHandler (registry-track only)."""
    from ga import GenericAgentHandler
    assert not hasattr(GenericAgentHandler, "do_skill_manage"), \
        "do_skill_manage must be migrated out of GenericAgentHandler"


def test_skill_manage_helpers_are_module_level():
    """The 3 helpers moved to tools.skill_manage as module-level functions."""
    import importlib

    import tools.skill_manage as sm
    importlib.reload(sm)
    for name in ("_validate_skill_content", "_set_frontmatter_flag", "_build_skill_brief"):
        assert hasattr(sm, name), f"tools.skill_manage.{name} missing"


def test_build_skill_brief_takes_cwd_param():
    """_build_skill_brief signature changed: (action, name, reason, path, cwd)."""
    import tools.skill_manage as sm
    brief = sm._build_skill_brief("patch", "s", "why", "/abs/.agents/skills/s/SKILL.md", "/abs")
    assert "patch" in brief and "s" in brief and "file_read" in brief
