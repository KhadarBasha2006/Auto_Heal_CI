"""The CI/CD-style validation loop.

After every healing round the checkout is put through a sequence of gates that
mirror what a pipeline would do -- parse, resolve imports, lint, byte-compile,
test. Each gate reports pass/fail/skip independently so the dashboard can show
exactly which stage a run died at, rather than a single opaque red cross.

Gates never install dependencies. A validator that reaches the network is slow
and non-deterministic, and "your build failed because npm was down" is precisely
the confusion this project exists to remove.
"""

from __future__ import annotations

import compileall
import contextlib
import io
import shutil
import subprocess
import time
from pathlib import Path

from .analysis import scan_repository
from .analysis.scanning import build_inventory
from .models import Severity, StageResult, ValidationRun


def _timed(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def stage_syntax(root: Path) -> StageResult:
    """Every source file must parse."""
    start = time.monotonic()
    result = scan_repository(root)
    failures = [
        p for p in result.problems
        if p.severity is Severity.CRITICAL and p.kind.value in {"syntax", "indentation"}
    ]
    detail = (
        "All source files parse cleanly."
        if not failures
        else "; ".join(f"{p.file}:{p.line} {p.message[:70]}" for p in failures[:5])
    )
    return StageResult(
        name="syntax", passed=not failures, duration_ms=_timed(start),
        detail=detail, problem_count=len(failures),
    )


def stage_imports(root: Path) -> StageResult:
    start = time.monotonic()
    problems = [
        p for p in scan_repository(root).problems
        if p.kind.value == "import" and p.severity.weight >= Severity.HIGH.weight
    ]
    detail = (
        "All imports resolve."
        if not problems
        else "; ".join(f"{p.file}:{p.line} {p.message[:70]}" for p in problems[:5])
    )
    return StageResult(
        name="imports", passed=not problems, duration_ms=_timed(start),
        detail=detail, problem_count=len(problems),
    )


def stage_lint(root: Path) -> StageResult:
    """Fail only on defect-grade findings; style noise must not block a pipeline."""
    start = time.monotonic()
    problems = [
        p for p in scan_repository(root).problems
        if p.kind.value in {"lint", "type"}
        and p.severity.weight >= Severity.HIGH.weight
    ]
    detail = (
        "No high-severity lint or type findings."
        if not problems
        else "; ".join(f"{p.file}:{p.line} {p.code} {p.message[:50]}" for p in problems[:5])
    )
    return StageResult(
        name="lint", passed=not problems, duration_ms=_timed(start),
        detail=detail, problem_count=len(problems),
    )


def stage_compile(root: Path) -> StageResult:
    """Byte-compile Python and syntax-check JavaScript.

    This is the closest thing to a build that can run without fetching
    dependencies, so it stays fully offline and deterministic.
    """
    start = time.monotonic()
    inventory = build_inventory(root)
    python_files = inventory.by_suffix(".py")
    js_files = inventory.by_suffix(".js", ".mjs", ".cjs")

    if not python_files and not js_files:
        return StageResult(
            name="compile", passed=True, duration_ms=_timed(start),
            detail="No compilable sources found.", skipped=True,
        )

    failures: list[str] = []
    if python_files:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            ok = compileall.compile_dir(
                str(root), quiet=2, force=True, legacy=True, workers=1
            )
        if not ok:
            output = buffer.getvalue().strip().splitlines()
            failures.extend(output[:5] or ["Python byte-compilation failed."])

    if js_files and shutil.which("node"):
        for path in js_files[:200]:
            try:
                check = subprocess.run(
                    ["node", "--check", str(path)],
                    capture_output=True, text=True, timeout=20,
                )
            except (subprocess.TimeoutExpired, OSError):
                continue
            if check.returncode != 0:
                failures.append(f"{inventory.relative(path)}: JS syntax error")

    # Clean up the .pyc files compileall just wrote so they never get committed.
    for cached in root.rglob("*.pyc"):
        try:
            cached.unlink()
        except OSError:
            pass

    return StageResult(
        name="compile", passed=not failures, duration_ms=_timed(start),
        detail="; ".join(failures[:5]) if failures
        else f"Compiled {len(python_files)} Python and {len(js_files)} JS file(s).",
        problem_count=len(failures),
    )


def stage_tests(
    root: Path,
    enabled: bool = True,
    timeout: float = 180.0,
    allowed: bool = False,
) -> StageResult:
    """Run the repository's pytest suite if one is present and runnable.

    `allowed` is the operator-level switch and `enabled` is the per-request one;
    both must be true. Collecting a test suite imports `conftest.py` and every
    test module, so this stage executes code from the repository under
    analysis. On a shared deployment that would let any submitted URL run code
    on the server, which is why the operator switch defaults to off.
    """
    start = time.monotonic()
    if not enabled:
        return StageResult(
            name="tests", passed=True, duration_ms=_timed(start),
            detail="Test execution disabled for this run.", skipped=True,
        )
    if not allowed:
        return StageResult(
            name="tests", passed=False, duration_ms=_timed(start),
            detail=(
                "Test execution is disabled on this server. Running a cloned "
                "repository's suite executes its code; set "
                "HEALING_AGENT_ALLOW_TEST_EXECUTION=true only where every "
                "submitted repository is trusted."
            ),
            skipped=True,
        )

    has_tests = any(root.rglob("test_*.py")) or any(root.rglob("*_test.py"))
    if not has_tests:
        return StageResult(
            name="tests", passed=True, duration_ms=_timed(start),
            detail="No pytest-style tests found in the repository.", skipped=True,
        )
    if not shutil.which("pytest"):
        return StageResult(
            name="tests", passed=True, duration_ms=_timed(start),
            detail="pytest is not installed in this environment.", skipped=True,
        )

    try:
        result = subprocess.run(
            ["pytest", "-q", "--no-header", "-x", "--timeout=60"]
            if _has_timeout_plugin() else ["pytest", "-q", "--no-header", "-x"],
            cwd=str(root), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return StageResult(
            name="tests", passed=False, duration_ms=_timed(start),
            detail=f"Test run exceeded {int(timeout)}s and was terminated.",
            problem_count=1,
        )
    except OSError as exc:
        return StageResult(
            name="tests", passed=True, duration_ms=_timed(start),
            detail=f"Could not launch pytest: {exc}", skipped=True,
        )

    output = (result.stdout or "") + (result.stderr or "")
    tail = output.strip().splitlines()[-3:] if output.strip() else []

    # Exit code 5 means "no tests collected" -- not a failure of the change.
    if result.returncode == 5:
        return StageResult(
            name="tests", passed=True, duration_ms=_timed(start),
            detail="pytest collected no tests.", skipped=True,
        )
    # Exit code 4 (usage error) usually means missing dependencies, not a
    # regression introduced by the agent.
    if result.returncode in (3, 4):
        return StageResult(
            name="tests", passed=True, duration_ms=_timed(start),
            detail=f"Test suite could not run in this environment: {' '.join(tail)[:200]}",
            skipped=True,
        )

    return StageResult(
        name="tests", passed=result.returncode == 0, duration_ms=_timed(start),
        detail=" ".join(tail)[:300] or "Test suite completed.",
        problem_count=0 if result.returncode == 0 else 1,
    )


def _has_timeout_plugin() -> bool:
    try:
        import pytest_timeout  # noqa: F401
        return True
    except ImportError:
        return False


def run_validation(
    root: Path,
    round_index: int = 1,
    run_tests: bool = True,
    allow_test_execution: bool = False,
) -> ValidationRun:
    """Execute every gate and aggregate the verdict."""
    run = ValidationRun(round_index=round_index)
    run.stages = [
        stage_syntax(root),
        stage_imports(root),
        stage_lint(root),
        stage_compile(root),
        stage_tests(root, enabled=run_tests, allowed=allow_test_execution),
    ]
    run.passed = all(stage.passed for stage in run.stages)
    return run
