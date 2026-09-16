"""Tier 1 repairs: rule-based fixes that need no model.

Everything here is deterministic and reversible in intent -- these are the
repairs where the correct output is fully implied by the defect, so spending a
model call on them would add latency and risk without adding accuracy. They
also mean the agent still fixes real problems when no API key is configured.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from ..analysis.lint import ruff_command
from ..models import Fix, FixTier, Problem, ProblemKind

# Rules ruff can fix safely (no semantic change).
AUTOFIX_SELECT = "F401,I001,W291,W292,W293,W391,E401,E711,E712,E713,E714,F632"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _changed_line_count(before: str, after: str) -> int:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    changed = sum(
        1 for a, b in zip(before_lines, after_lines, strict=False) if a != b
    )
    return changed + abs(len(before_lines) - len(after_lines))


# ------------------------------------------------------------- indentation --


def _multiline_string_lines(source: str) -> set[int]:
    """Line numbers occupied by multi-line string literals.

    Leading whitespace inside such a literal is part of the data, so the
    indentation rewriter must leave those lines alone.
    """
    protected: set[int] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return protected
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            if end > node.lineno:
                # Only the CONTINUATION lines hold string content. The opening
                # line's leading whitespace is ordinary code indentation, and
                # protecting it would leave the file mixing tabs and spaces in
                # the same block -- which is exactly the defect being fixed.
                protected.update(range(node.lineno + 1, end + 1))
    return protected


def _dominant_indent_unit(source: str) -> int:
    """Infer the file's space-indent step, defaulting to 4."""
    widths = Counter()
    for line in source.splitlines():
        if not line.strip() or not line.startswith(" "):
            continue
        widths[len(line) - len(line.lstrip(" "))] += 1
    if not widths:
        return 4
    for candidate in (4, 2, 8, 3):
        if any(width % candidate == 0 for width in widths):
            return candidate
    return 4


def fix_mixed_indentation(path: Path, problems: list[Problem]) -> Fix | None:
    """Normalise a file that mixes tab and space indentation onto spaces.

    Only the leading whitespace of each line is rewritten, and lines inside
    multi-line strings are skipped so string contents are never altered.
    """
    source = _read(path)
    if source is None or "\t" not in source:
        return None

    protected = _multiline_string_lines(source)
    unit = _dominant_indent_unit(source)
    replacement = " " * unit

    out: list[str] = []
    touched = 0
    for number, line in enumerate(source.splitlines(keepends=True), start=1):
        if number in protected or not line.startswith("\t"):
            out.append(line)
            continue
        stripped = line.lstrip("\t")
        depth = len(line) - len(stripped)
        out.append(replacement * depth + stripped)
        touched += 1

    if not touched:
        return None
    rewritten = "".join(out)

    # Refuse the change if it would break a file that previously parsed.
    if path.suffix == ".py":
        try:
            ast.parse(source)
        except SyntaxError:
            pass
        else:
            try:
                ast.parse(rewritten)
            except SyntaxError:
                return None

    _write(path, rewritten)
    return Fix(
        file=str(path.name),
        tier=FixTier.DETERMINISTIC,
        description=(
            f"Converted {touched} tab-indented line(s) to {unit}-space "
            f"indentation, removing the tab/space mix."
        ),
        problems_addressed=[p.key for p in problems if p.code == "MIXED-INDENT"],
        lines_changed=touched,
    )


# --------------------------------------------------------------------- json --


def _strip_json_trailing_commas(text: str) -> str:
    """Remove commas that sit immediately before a closing brace or bracket.

    String literals are tracked so a comma inside a string is never touched.
    """
    out: list[str] = []
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            out.append(char)
            continue

        if char == ",":
            rest = text[index + 1:]
            stripped = rest.lstrip(" \t\r\n")
            if stripped[:1] in ("}", "]"):
                continue  # drop this trailing comma
        out.append(char)

    return "".join(out)


def fix_json_syntax(path: Path, problems: list[Problem]) -> Fix | None:
    """Repair the one JSON defect with an unambiguous fix: trailing commas."""
    source = _read(path)
    if source is None:
        return None
    try:
        json.loads(source)
        return None  # already valid
    except json.JSONDecodeError:
        pass

    candidate = _strip_json_trailing_commas(source)
    if candidate == source:
        return None
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        return None  # more than trailing commas is wrong; leave it to the model

    _write(path, candidate)
    return Fix(
        file=path.name,
        tier=FixTier.DETERMINISTIC,
        description="Removed trailing comma(s) that made the JSON invalid.",
        problems_addressed=[p.key for p in problems if p.code == "JSON-PARSE"],
        lines_changed=_changed_line_count(source, candidate),
    )


# --------------------------------------------------------------- whitespace --


def fix_final_newline(path: Path) -> bool:
    source = _read(path)
    if source is None or not source or source.endswith("\n"):
        return False
    _write(path, source + "\n")
    return True


# --------------------------------------------------------------------- ruff --


def run_ruff_autofix(root: Path, timeout: float = 180.0) -> tuple[int, str]:
    """Apply ruff's safe autofixes across the checkout.

    Returns the number of fixes applied and ruff's summary line. Unsafe fixes
    are deliberately not enabled: they can change behaviour, and this agent
    must never trade a lint warning for a behavioural regression.
    """
    command = ruff_command()
    if command is None:
        return 0, "ruff is not available in this environment"
    try:
        result = subprocess.run(
            [
                *command, "check", "--isolated", "--select", AUTOFIX_SELECT,
                "--fix", "--no-cache", ".",
            ],
            cwd=str(root), capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 0, f"ruff autofix unavailable: {exc}"

    output = (result.stdout or "") + (result.stderr or "")
    match = re.search(r"Fixed (\d+) error", output)
    count = int(match.group(1)) if match else 0
    summary = match.group(0) if match else "no fixes applied"
    return count, summary


# ---------------------------------------------------------------- dispatch --


def apply_deterministic_fixes(
    root: Path, problems: list[Problem], round_index: int = 1
) -> list[Fix]:
    """Run every tier-1 repair that applies to the detected problems."""
    fixes: list[Fix] = []
    by_file: dict[str, list[Problem]] = {}
    for problem in problems:
        by_file.setdefault(problem.file, []).append(problem)

    # Indentation and JSON repairs are per-file and targeted.
    for rel, file_problems in by_file.items():
        path = root / rel
        if not path.is_file():
            continue
        codes = {p.code for p in file_problems}

        if "MIXED-INDENT" in codes or any(
            p.kind is ProblemKind.INDENTATION and p.code == "W191"
            for p in file_problems
        ):
            fix = fix_mixed_indentation(path, file_problems)
            if fix:
                fix.file = rel
                fix.round_index = round_index
                fixes.append(fix)

        if "JSON-PARSE" in codes:
            fix = fix_json_syntax(path, file_problems)
            if fix:
                fix.file = rel
                fix.round_index = round_index
                fixes.append(fix)

    # Ruff's safe autofixes run repo-wide in one pass.
    fixed_count, summary = run_ruff_autofix(root)
    if fixed_count:
        autofixable = [
            p.key for p in problems
            if p.auto_fixable and p.detector == "ruff"
        ]
        fixes.append(
            Fix(
                file="(repository-wide)",
                tier=FixTier.DETERMINISTIC,
                description=(
                    f"Applied {fixed_count} safe ruff autofix(es): unused "
                    f"imports removed, import blocks sorted, trailing "
                    f"whitespace and comparison-style errors corrected."
                ),
                problems_addressed=autofixable[:fixed_count],
                lines_changed=fixed_count,
                round_index=round_index,
            )
        )
    return fixes
