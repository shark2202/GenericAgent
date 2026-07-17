"""Regression: ga_utils extraction is a pure move. Symbols must remain importable
from ga (back-compat for agentmain.py:14 `from ga import ...`) and from ga_utils,
with unchanged behavior."""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_ga_reexports_utils_symbols():
    """External code does `from ga import smart_format, ...`; must still work.
    Covers agentmain.py:14 contract: GenericAgentHandler, smart_format,
    get_global_memory, format_error, consume_file."""
    import ga
    for name in ("smart_format", "format_error", "consume_file", "code_run",
                 "file_read", "file_patch", "safe_print", "ask_user",
                 "get_global_memory", "expand_file_refs", "log_memory_access",
                 "web_scan", "web_execute_js", "first_init_driver",
                 "script_dir", "driver", "_read_dirs"):
        assert hasattr(ga, name), f"ga.{name} missing after utils extraction"


def test_ga_utils_module_has_moved_symbols():
    """The moved symbols now live in ga_utils."""
    import ga_utils
    for name in ("smart_format", "format_error", "consume_file", "code_run",
                 "file_read", "file_patch", "safe_print", "ask_user",
                 "expand_file_refs", "log_memory_access",
                 "web_scan", "web_execute_js", "first_init_driver",
                 "script_dir", "driver", "_read_dirs", "_scan_files"):
        assert hasattr(ga_utils, name), f"ga_utils.{name} missing"


def test_smart_format_behavior_unchanged():
    """Pure move: same input → same output on both modules (identity, since re-export)."""
    from ga import smart_format as ga_sf
    from ga_utils import smart_format as gu_sf
    payload = {"k": "x" * 200}
    assert ga_sf(payload) == gu_sf(payload)
    assert " ... " in ga_sf(payload)


def test_format_error_behavior_unchanged():
    """format_error on the same exception produces identical output from both modules."""
    from ga import format_error as ga_fe
    from ga_utils import format_error as gu_fe
    try:
        1 / 0  # noqa: B018 — intentionally raise to exercise format_error
    except Exception as e:
        a, b = ga_fe(e), gu_fe(e)
    assert a == b
    assert "ZeroDivisionError" in a


def test_safe_print_swallows_error():
    """safe_print must never raise even on a broken write target."""
    from ga_utils import safe_print
    safe_print("ok")  # no exception


def test_smart_format_is_same_object():
    """Named import re-export: ga.smart_format IS ga_utils.smart_format (same function object)."""
    from ga import smart_format as ga_sf
    from ga_utils import smart_format as gu_sf
    assert ga_sf is gu_sf


def test_ga_handler_class_still_importable():
    """GenericAgentHandler must remain defined in ga (only utils moved, not the class)."""
    from ga import GenericAgentHandler
    assert GenericAgentHandler.__name__ == "GenericAgentHandler"


def test_ga_py_line_count_dropped_significantly():
    """Extraction goal: ga.py shrinks meaningfully (utils moved out).
    Pre-extraction ga.py was 838 lines; after extraction it must be well under that."""
    import ga as _ga
    ga_path = _ga.__file__
    with open(ga_path, encoding="utf-8") as f:
        n = sum(1 for _ in f)
    assert n < 700, f"ga.py still {n} lines; utils extraction did not shrink it enough"
