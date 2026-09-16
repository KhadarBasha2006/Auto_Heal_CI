"""Repairs must fix real defects and must never make a file worse."""

from pathlib import Path

from healing_agent.analysis import scan_repository
from healing_agent.healing import apply_deterministic_fixes
from healing_agent.healing.ai import public_symbols, rewrite_destroys_code
from healing_agent.healing.deterministic import (
    _strip_json_trailing_commas,
    fix_mixed_indentation,
)


def test_deterministic_pass_reduces_problems(broken_repo: Path):
    before = scan_repository(broken_repo).problems
    fixes = apply_deterministic_fixes(broken_repo, before)
    after = scan_repository(broken_repo).problems

    assert fixes, "deterministic tier must apply at least one repair"
    assert len(after) < len(before)


def test_json_trailing_comma_repair():
    assert _strip_json_trailing_commas('{"a": 1,}') == '{"a": 1}'
    assert _strip_json_trailing_commas('{"a": [1, 2,],}') == '{"a": [1, 2]}'
    # A comma inside a string is data, not syntax.
    assert _strip_json_trailing_commas('{"a": "x,}"}') == '{"a": "x,}"}'


def test_indentation_fix_preserves_multiline_strings(tmp_path: Path):
    """Leading tabs inside a docstring are content and must survive."""
    path = tmp_path / "mod.py"
    path.write_text(
        'def f():\n'
        '\tdoc = """\n'
        '\tkeep this tab\n'
        '"""\n'
        '\treturn doc\n'
    )
    original = path.read_text()
    fix_mixed_indentation(path, [])
    updated = path.read_text()

    assert "\tkeep this tab" in updated, "string contents must not be rewritten"
    assert updated != original


def test_indentation_fix_never_breaks_a_parsing_file(broken_repo: Path):
    path = broken_repo / "src" / "pkg" / "calc.py"
    import ast

    ast.parse(path.read_text())          # parses before
    fix_mixed_indentation(path, [])
    ast.parse(path.read_text())          # still parses after


def test_public_symbols_extraction():
    source = "def a():\n    pass\n\n\nclass B:\n    def c(self):\n        pass\n"
    assert public_symbols(source) == {"a", "B", "c"}


def test_rewrite_that_deletes_code_is_rejected():
    """Deleting the failing function must never count as a repair."""
    source = (
        "def a():\n    return 1\n\n\n"
        "def b():\n    return undefined_thing()\n\n\n"
        "def c():\n    return 3\n"
    )
    deleted = "def a():\n    return 1\n\n\ndef c():\n    return 3\n"
    reason = rewrite_destroys_code(source, deleted, ".py")
    assert reason and "removed definitions" in reason


def test_rewrite_that_guts_the_file_is_rejected():
    source = "\n".join(f"line_{i} = {i}" for i in range(20))
    assert rewrite_destroys_code(source, "pass\n", ".py")


def test_genuine_repair_is_accepted():
    source = "def a():\n    return undefined_thing()\n"
    repaired = "def a():\n    return 2\n"
    assert rewrite_destroys_code(source, repaired, ".py") is None
