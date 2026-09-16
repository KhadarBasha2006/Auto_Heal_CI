"""Run scoring must reward real repair, not activity."""

from healing_agent.models import (
    Problem,
    ProblemKind,
    Severity,
    StageResult,
    ValidationRun,
)
from healing_agent.scoring import compute_score


def _problem(severity: Severity) -> Problem:
    return Problem(
        file="a.py", line=1, column=1, kind=ProblemKind.LINT,
        severity=severity, code="X", message="m", detector="d",
    )


def _validation(passed: bool) -> ValidationRun:
    return ValidationRun(
        round_index=1, passed=passed,
        stages=[
            StageResult("syntax", True, 10, "ok"),
            StageResult("lint", passed, 10, "ok"),
            StageResult("tests", True, 0, "skipped", skipped=True),
        ],
    )


def test_perfect_run_scores_full_marks():
    score = compute_score(
        [_problem(Severity.CRITICAL), _problem(Severity.LOW)], [], 5, 10.0,
        [_validation(True)],
    )
    assert score["total"] == 100 and score["grade"] == "A+"


def test_fixing_nothing_scores_poorly():
    problems = [_problem(Severity.CRITICAL)]
    score = compute_score(problems, problems, 0, 300.0, [_validation(False)])
    assert score["total"] < 40


def test_severity_is_weighted_not_counted():
    """Clearing one critical must beat clearing several trivial findings."""
    before = [_problem(Severity.CRITICAL)] + [_problem(Severity.LOW)] * 5
    cleared_critical = compute_score(before, [_problem(Severity.LOW)] * 5, 1, 10.0,
                                     [_validation(True)])
    cleared_trivia = compute_score(before, [_problem(Severity.CRITICAL)], 5, 10.0,
                                   [_validation(True)])
    assert cleared_critical["total"] > cleared_trivia["total"]


def test_speed_affects_the_score():
    problems = [_problem(Severity.HIGH)]
    fast = compute_score(problems, [], 1, 10.0, [_validation(True)])
    slow = compute_score(problems, [], 1, 400.0, [_validation(True)])
    assert fast["total"] > slow["total"]


def test_score_is_bounded_and_explained():
    score = compute_score([_problem(Severity.HIGH)], [], 1, 10.0, [_validation(True)])
    assert 0 <= score["total"] <= 100
    assert len(score["breakdown"]) == 4
    assert all(item["detail"] for item in score["breakdown"])
