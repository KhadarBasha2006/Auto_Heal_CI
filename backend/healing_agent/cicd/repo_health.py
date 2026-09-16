"""Repository health monitoring.

A pipeline's reliability is partly a property of the repository around it, so
the agent grades the things that make outages more likely or harder to recover
from: no CI at all, no tests, undeclared dependencies, a stale default branch,
a pile-up of open pull requests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..github import GitHubClient, GitHubError


@dataclass
class HealthCheck:
    name: str
    passed: bool
    detail: str
    weight: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "weight": self.weight,
        }


@dataclass
class RepoHealth:
    checks: list[HealthCheck] = field(default_factory=list)
    score: int = 0
    grade: str = "n/a"
    signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "grade": self.grade,
            "checks": [check.to_dict() for check in self.checks],
            "signals": self.signals,
            "passed": sum(1 for c in self.checks if c.passed),
            "total": len(self.checks),
        }


def _grade(score: int) -> str:
    for threshold, letter in ((90, "A"), (80, "B"), (70, "C"), (55, "D")):
        if score >= threshold:
            return letter
    return "F"


def assess_repo_health(
    root: Path,
    client: GitHubClient | None = None,
    owner: str = "",
    repo: str = "",
) -> RepoHealth:
    """Grade the repository on the practices that keep a pipeline recoverable."""
    health = RepoHealth()
    add = health.checks.append

    workflows_dir = root / ".github" / "workflows"
    workflows = (
        sorted(workflows_dir.glob("*.y*ml")) if workflows_dir.is_dir() else []
    )
    add(
        HealthCheck(
            "ci_configured",
            bool(workflows),
            f"{len(workflows)} workflow file(s) found"
            if workflows
            else "No GitHub Actions workflows - failures go unnoticed until release",
            weight=3,
        )
    )

    has_tests = any(root.rglob("test_*.py")) or any(root.rglob("*_test.py")) or (
        (root / "tests").is_dir() or (root / "test").is_dir()
    )
    add(
        HealthCheck(
            "tests_present",
            has_tests,
            "Test files present" if has_tests else "No test suite detected",
            weight=3,
        )
    )

    manifests = [
        name
        for name in (
            "requirements.txt", "pyproject.toml", "setup.py", "Pipfile",
            "package.json", "go.mod", "Cargo.toml", "pom.xml", "Gemfile",
        )
        if (root / name).is_file()
    ]
    add(
        HealthCheck(
            "dependencies_declared",
            bool(manifests),
            f"Manifest(s): {', '.join(manifests)}"
            if manifests
            else "No dependency manifest - builds are not reproducible",
            weight=2,
        )
    )

    has_readme = any((root / name).is_file() for name in ("README.md", "README.rst", "README"))
    add(HealthCheck("readme_present", has_readme,
                    "README present" if has_readme else "No README", weight=1))

    has_ignore = (root / ".gitignore").is_file()
    add(HealthCheck("gitignore_present", has_ignore,
                    ".gitignore present" if has_ignore
                    else "No .gitignore - build artifacts risk being committed", weight=1))

    pinned = False
    requirements = root / "requirements.txt"
    if requirements.is_file():
        try:
            text = requirements.read_text(encoding="utf-8")
            entries = [
                line for line in text.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            pinned = bool(entries) and sum("==" in line for line in entries) >= len(entries) * 0.5
        except (OSError, UnicodeDecodeError):
            pinned = False
        add(
            HealthCheck(
                "dependencies_pinned", pinned,
                "Most dependencies pinned to exact versions" if pinned
                else "Dependencies largely unpinned - upstream releases can break CI without a commit",
                weight=2,
            )
        )

    # ---- Remote signals, when a token is available -------------------------
    if client and owner and repo:
        try:
            info = client.get_repo(owner, repo)
            health.signals["default_branch"] = info.get("default_branch")
            health.signals["private"] = info.get("private")
            health.signals["open_issues"] = info.get("open_issues_count")
            pushed_at = info.get("pushed_at")
            if pushed_at:
                try:
                    last_push = datetime.fromisoformat(str(pushed_at).replace("Z", "+00:00"))
                    days = (datetime.now(UTC) - last_push).days
                    health.signals["days_since_last_push"] = days
                    add(
                        HealthCheck(
                            "actively_maintained", days <= 180,
                            f"Last push {days} day(s) ago",
                            weight=1,
                        )
                    )
                except ValueError:
                    pass
        except GitHubError as exc:
            health.signals["repo_lookup_error"] = str(exc)[:200]

        try:
            pulls = client.list_open_pulls(owner, repo)
            health.signals["open_pull_requests"] = len(pulls)
            add(
                HealthCheck(
                    "pr_backlog_controlled", len(pulls) <= 25,
                    f"{len(pulls)} open pull request(s)",
                    weight=1,
                )
            )
        except GitHubError:
            pass

    earned = sum(check.weight for check in health.checks if check.passed)
    possible = sum(check.weight for check in health.checks) or 1
    health.score = round(earned / possible * 100)
    health.grade = _grade(health.score)
    return health
