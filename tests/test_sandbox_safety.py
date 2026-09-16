"""A shared deployment must not execute code from a submitted repository.

Collecting a pytest suite imports `conftest.py` and every test module, so the
tests stage is the one place repository code would run. It is gated behind an
operator switch that defaults to off.
"""

from pathlib import Path

import pytest
from healing_agent.config import Settings
from healing_agent.validation import run_validation, stage_tests


@pytest.fixture
def malicious_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A repo whose conftest.py writes a file the moment pytest collects it."""
    root = tmp_path / "repo"
    root.mkdir()
    marker = tmp_path / "PWNED.txt"
    (root / "conftest.py").write_text(
        f"from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('code executed')\n"
    )
    (root / "test_thing.py").write_text("def test_ok():\n    assert True\n")
    return root, marker


def test_execution_is_off_by_default(malicious_repo):
    root, marker = malicious_repo
    result = stage_tests(root, enabled=True)          # operator switch defaults off
    assert result.skipped
    assert not marker.exists(), "repository code must not run by default"


def test_run_validation_does_not_execute_repo_code(malicious_repo):
    root, marker = malicious_repo
    run = run_validation(root, run_tests=True)        # allow_test_execution defaults False
    stage = next(s for s in run.stages if s.name == "tests")
    assert stage.skipped
    assert not marker.exists(), "run_validation must not run repository code"


def test_settings_default_to_refusing_execution():
    assert Settings().allow_test_execution is False


def test_request_flag_alone_cannot_enable_execution(malicious_repo):
    """A per-request flag must not override the operator's decision."""
    root, marker = malicious_repo
    run = run_validation(root, run_tests=True, allow_test_execution=False)
    assert next(s for s in run.stages if s.name == "tests").skipped
    assert not marker.exists()


def test_operator_can_still_opt_in(malicious_repo):
    """Explicit opt-in works, for trusted local use where it is the point."""
    root, _ = malicious_repo
    result = stage_tests(root, enabled=True, allowed=True)
    assert not result.skipped or "pytest is not installed" in result.detail
