"""Lint and style detection via ruff.

Ruff is invoked with `--isolated` so the target repository's own configuration
cannot change which rules run. That keeps findings comparable between repos and
stops a hostile or simply unusual config from suppressing real defects.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from ..models import Problem, ProblemKind, Severity

# Rules selected for the main pass. E501 (line length) is excluded because it
# is a formatting preference, not a defect, and would swamp the report.
STABLE_SELECT = "E,W,F,I"
IGNORED = "E501,W605"
# Indentation rules live behind ruff's preview gate.
INDENT_SELECT = "E1,W1"

SEVERITY_BY_CODE: dict[str, Severity] = {
    "invalid-syntax": Severity.CRITICAL,
    "E999": Severity.CRITICAL,
    "F821": Severity.CRITICAL,   # undefined name -> NameError at runtime
    "F822": Severity.CRITICAL,   # undefined name in __all__
    "F823": Severity.CRITICAL,   # local referenced before assignment
    "F811": Severity.HIGH,       # redefinition shadows earlier binding
    "F502": Severity.HIGH, "F506": Severity.HIGH, "F507": Severity.HIGH,
    "F601": Severity.HIGH, "F602": Severity.HIGH,
    "F632": Severity.HIGH,       # `is` against a literal
    "F701": Severity.HIGH, "F702": Severity.HIGH,
    "E711": Severity.MEDIUM, "E712": Severity.MEDIUM, "E713": Severity.MEDIUM,
    "E714": Severity.MEDIUM, "E721": Severity.MEDIUM, "E722": Severity.MEDIUM,
    "E731": Severity.MEDIUM, "E741": Severity.MEDIUM,
    "F841": Severity.MEDIUM,     # assigned but never used
    "F401": Severity.LOW,        # unused import
    "I001": Severity.LOW,        # unsorted imports
    "W291": Severity.LOW, "W292": Severity.LOW, "W293": Severity.LOW,
    "W391": Severity.LOW,
}

KIND_BY_PREFIX: tuple[tuple[str, ProblemKind], ...] = (
    ("E1", ProblemKind.INDENTATION),
    ("W1", ProblemKind.INDENTATION),
    ("E9", ProblemKind.SYNTAX),
    ("F4", ProblemKind.IMPORT),
    ("F8", ProblemKind.TYPE),
    ("I0", ProblemKind.IMPORT),
    ("W2", ProblemKind.FORMATTING),
    ("W3", ProblemKind.FORMATTING),
    ("E2", ProblemKind.FORMATTING),
    ("E3", ProblemKind.FORMATTING),
)


def ruff_command() -> list[str] | None:
    """How to invoke ruff here, or None if it is unavailable.

    Prefers the console script, but falls back to `python -m ruff`: on
    serverless runtimes the wheel's entry-point script is often not on PATH
    even though the package is installed, and without this fallback every lint
    and type finding would silently disappear from the report.
    """
    if shutil.which("ruff"):
        return ["ruff"]
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode == 0:
        return [sys.executable, "-m", "ruff"]
    return None


def ruff_available() -> bool:
    return ruff_command() is not None


def _classify(code: str) -> tuple[ProblemKind, Severity]:
    severity = SEVERITY_BY_CODE.get(code, Severity.LOW)
    if code in {"invalid-syntax", "E999"}:
        return ProblemKind.SYNTAX, severity
    for prefix, kind in KIND_BY_PREFIX:
        if code.startswith(prefix):
            return kind, severity
    return ProblemKind.LINT, severity


def _run_ruff(root: Path, args: list[str], timeout: float = 180.0) -> list[dict]:
    command = ruff_command()
    if command is None:
        return []
    try:
        result = subprocess.run(
            [*command, "check", *args, "--output-format", "json", "--no-cache", "."],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if not result.stdout.strip():
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def detect_lint_problems(
    root: Path, skip: set[str], include_indentation: bool = True
) -> list[Problem]:
    """Run ruff and translate its findings into `Problem` records.

    `skip` holds files that failed to parse; the Python AST detector already
    reported a precise syntax error for those, so ruff's cascade of follow-on
    complaints about the same file is dropped.
    """
    if not ruff_available():
        return []

    findings = _run_ruff(
        root, ["--isolated", "--select", STABLE_SELECT, "--ignore", IGNORED]
    )
    if include_indentation:
        findings += _run_ruff(
            root,
            [
                "--isolated", "--preview",
                "--select", INDENT_SELECT, "--ignore", IGNORED,
            ],
        )

    problems: list[Problem] = []
    seen: set[tuple[str, int, str]] = set()

    for item in findings:
        code = item.get("code") or "unknown"
        filename = item.get("filename") or ""
        try:
            rel = str(Path(filename).relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = Path(filename).name
        if rel in skip:
            continue

        location = item.get("location") or {}
        line = int(location.get("row") or 1)
        key = (rel, line, code)
        if key in seen:
            continue
        seen.add(key)

        kind, severity = _classify(code)
        problems.append(
            Problem(
                file=rel,
                line=line,
                column=int(location.get("column") or 1),
                kind=kind,
                severity=severity,
                code=code,
                message=item.get("message") or "",
                detector="ruff",
                auto_fixable=bool(item.get("fix")),
            )
        )
    return problems
