"""Defect detection across the repository."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..models import Problem
from .config_checks import (
    detect_javascript_syntax,
    detect_json_problems,
    detect_yaml_problems,
)
from .lint import detect_lint_problems, ruff_available
from .python_checks import (
    detect_import_problems,
    detect_indentation_problems,
    detect_syntax_problems,
)
from .scanning import RepoInventory, build_inventory

__all__ = [
    "ScanResult",
    "RepoInventory",
    "build_inventory",
    "ruff_available",
    "scan_repository",
]


@dataclass
class ScanResult:
    problems: list[Problem] = field(default_factory=list)
    inventory: RepoInventory | None = None
    unparseable: set[str] = field(default_factory=set)
    detectors_run: list[str] = field(default_factory=list)
    detectors_skipped: list[str] = field(default_factory=list)

    @property
    def files_scanned(self) -> int:
        return len(self.inventory.files) if self.inventory else 0


def scan_repository(
    root: Path, max_files: int = 4000, inventory: RepoInventory | None = None
) -> ScanResult:
    """Run every applicable detector over the checkout.

    Order matters: syntax runs first so that files which cannot be parsed are
    excluded from the detectors that would otherwise emit a cascade of
    meaningless follow-on findings for the same root cause.
    """
    inventory = inventory or build_inventory(root, max_files=max_files)
    result = ScanResult(inventory=inventory)

    syntax_problems, unparseable = detect_syntax_problems(inventory)
    result.problems.extend(syntax_problems)
    result.unparseable = unparseable
    result.detectors_run.append("python-ast")

    result.problems.extend(detect_indentation_problems(inventory, unparseable))
    result.detectors_run.append("indentation")

    result.problems.extend(detect_import_problems(inventory, unparseable))
    result.detectors_run.append("import-graph")

    if ruff_available():
        result.problems.extend(detect_lint_problems(root, unparseable))
        result.detectors_run.append("ruff")
    else:
        result.detectors_skipped.append("ruff (binary not found)")

    result.problems.extend(detect_javascript_syntax(inventory))
    result.detectors_run.append("node --check")

    result.problems.extend(detect_json_problems(inventory))
    result.problems.extend(detect_yaml_problems(inventory))
    result.detectors_run.extend(["json", "yaml/workflow"])

    result.problems.sort(
        key=lambda p: (-p.severity.weight, p.file, p.line)
    )
    return result
