"""The detectors must find every defect category the agent claims to handle."""

from pathlib import Path

from healing_agent.analysis import scan_repository
from healing_agent.models import ProblemKind, Severity


def test_finds_every_defect_category(broken_repo: Path):
    result = scan_repository(broken_repo)
    kinds = {problem.kind for problem in result.problems}

    assert ProblemKind.SYNTAX in kinds, "syntax errors must be detected"
    assert ProblemKind.INDENTATION in kinds, "indentation problems must be detected"
    assert ProblemKind.IMPORT in kinds, "import errors must be detected"
    assert ProblemKind.TYPE in kinds, "type/name problems must be detected"
    assert ProblemKind.LINT in kinds, "lint issues must be detected"
    assert ProblemKind.CONFIG in kinds, "config problems must be detected"


def test_syntax_error_is_critical_and_located(broken_repo: Path):
    problems = scan_repository(broken_repo).problems
    syntax = [p for p in problems if p.file.endswith("broken_syntax.py")]
    assert syntax, "the unparseable file must be reported"
    assert syntax[0].severity is Severity.CRITICAL
    assert syntax[0].line == 2


def test_unresolved_relative_import_is_reported(broken_repo: Path):
    codes = {p.code for p in scan_repository(broken_repo).problems}
    assert "UNRESOLVED-RELATIVE-IMPORT" in codes


def test_mixed_indentation_is_reported(broken_repo: Path):
    codes = {p.code for p in scan_repository(broken_repo).problems}
    assert "MIXED-INDENT" in codes


def test_unparseable_file_does_not_cascade(broken_repo: Path):
    """A file that cannot be parsed yields one root-cause finding, not dozens."""
    result = scan_repository(broken_repo)
    from_broken = [p for p in result.problems if p.file.endswith("broken_syntax.py")]
    assert len(from_broken) == 1


def test_workflow_directory_is_scanned(broken_repo: Path):
    """.github must not be excluded by the .git filter -- the CI agent needs it."""
    result = scan_repository(broken_repo)
    files = {result.inventory.relative(f) for f in result.inventory.files}
    assert any(f.startswith(".github/workflows/") for f in files)


def test_clean_repository_reports_nothing(clean_repo: Path):
    assert scan_repository(clean_repo).problems == []
