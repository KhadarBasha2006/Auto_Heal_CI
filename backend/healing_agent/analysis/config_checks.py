"""Non-Python defect detection: JavaScript syntax, JSON, YAML, and workflows."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from ..models import Problem, ProblemKind, Severity
from .scanning import RepoInventory

try:
    import yaml

    _YAML = True
except ImportError:  # pragma: no cover - optional dependency
    _YAML = False


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def detect_javascript_syntax(inventory: RepoInventory) -> list[Problem]:
    """Use `node --check` to catch JavaScript parse errors.

    Skipped silently when node is unavailable. TypeScript is not checked here
    because `node --check` cannot parse type annotations.
    """
    if not shutil.which("node"):
        return []

    problems: list[Problem] = []
    for path in inventory.by_suffix(".js", ".mjs", ".cjs"):
        rel = inventory.relative(path)
        try:
            result = subprocess.run(
                ["node", "--check", str(path)],
                capture_output=True, text=True, timeout=20,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0:
            continue

        stderr = result.stderr or ""
        # node reports the offending location as "<path>:<line>" on its own
        # line. If stderr never names this file, the failure came from
        # somewhere else (a malformed package.json, for instance) and blaming
        # this file would be a false positive -- so skip it.
        location = re.search(
            rf"^{re.escape(str(path))}:(\d+)$", stderr, re.MULTILINE
        )
        if not location:
            continue
        line = int(location.group(1))

        detail = ""
        for entry in stderr.splitlines():
            stripped = entry.strip()
            if re.match(r"^[A-Za-z]*Error: ", stripped):
                detail = stripped
                break

        problems.append(
            Problem(
                file=rel, line=line, column=1, kind=ProblemKind.SYNTAX,
                severity=Severity.CRITICAL, code="JS-SYNTAX",
                message=detail or "JavaScript syntax error",
                detector="node --check", auto_fixable=False,
            )
        )
    return problems


def detect_json_problems(inventory: RepoInventory) -> list[Problem]:
    problems: list[Problem] = []
    for path in inventory.by_suffix(".json"):
        rel = inventory.relative(path)
        text = _read(path)
        if text is None or not text.strip():
            continue
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            severity = (
                Severity.CRITICAL
                if path.name in {"package.json", "tsconfig.json", "composer.json"}
                else Severity.HIGH
            )
            problems.append(
                Problem(
                    file=rel, line=exc.lineno, column=exc.colno,
                    kind=ProblemKind.CONFIG, severity=severity,
                    code="JSON-PARSE", message=f"Invalid JSON: {exc.msg}",
                    detector="json", auto_fixable=False,
                )
            )
    return problems


def detect_yaml_problems(inventory: RepoInventory) -> list[Problem]:
    """Validate YAML, treating GitHub workflow files as critical.

    A malformed workflow is a pipeline outage in waiting -- Actions refuses to
    schedule the run at all -- so it is graded above an ordinary config error.
    """
    if not _YAML:
        return []

    problems: list[Problem] = []
    for path in inventory.by_suffix(".yml", ".yaml"):
        rel = inventory.relative(path)
        text = _read(path)
        if text is None or not text.strip():
            continue
        is_workflow = ".github/workflows/" in rel.replace("\\", "/")
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            problems.append(
                Problem(
                    file=rel,
                    line=(mark.line + 1) if mark else 1,
                    column=(mark.column + 1) if mark else 1,
                    kind=ProblemKind.CONFIG,
                    severity=Severity.CRITICAL if is_workflow else Severity.HIGH,
                    code="WORKFLOW-PARSE" if is_workflow else "YAML-PARSE",
                    message=(
                        f"Invalid YAML: {getattr(exc, 'problem', str(exc))}"
                        + (
                            " - GitHub Actions cannot schedule this workflow."
                            if is_workflow
                            else ""
                        )
                    ),
                    detector="yaml", auto_fixable=False,
                )
            )
            continue

        if is_workflow and isinstance(document, dict):
            problems.extend(_check_workflow_shape(rel, document))
    return problems


def _check_workflow_shape(rel: str, document: dict) -> list[Problem]:
    """Structural checks on a parsed GitHub Actions workflow."""
    problems: list[Problem] = []
    # PyYAML parses a bare `on:` key as the boolean True.
    has_trigger = "on" in document or True in document
    if not has_trigger:
        problems.append(
            Problem(
                file=rel, line=1, column=1, kind=ProblemKind.CONFIG,
                severity=Severity.CRITICAL, code="WORKFLOW-NO-TRIGGER",
                message="Workflow declares no 'on:' trigger, so it will never run.",
                detector="workflow", auto_fixable=False,
            )
        )
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        problems.append(
            Problem(
                file=rel, line=1, column=1, kind=ProblemKind.CONFIG,
                severity=Severity.CRITICAL, code="WORKFLOW-NO-JOBS",
                message="Workflow defines no jobs.",
                detector="workflow", auto_fixable=False,
            )
        )
        return problems

    for name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        if "runs-on" not in job and "uses" not in job:
            problems.append(
                Problem(
                    file=rel, line=1, column=1, kind=ProblemKind.CONFIG,
                    severity=Severity.HIGH, code="WORKFLOW-NO-RUNNER",
                    message=(
                        f"Job '{name}' specifies neither 'runs-on' nor 'uses'; "
                        f"Actions cannot assign it a runner."
                    ),
                    detector="workflow", auto_fixable=False,
                )
            )
    return problems
