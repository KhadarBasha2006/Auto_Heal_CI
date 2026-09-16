"""Python-specific defect detection: syntax, indentation, and imports."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

from ..models import Problem, ProblemKind, Severity
from .scanning import RepoInventory

# Import name -> distribution name, for the cases where they differ.
IMPORT_TO_DISTRIBUTION: dict[str, str] = {
    "yaml": "pyyaml", "cv2": "opencv-python", "sklearn": "scikit-learn",
    "PIL": "pillow", "bs4": "beautifulsoup4", "dateutil": "python-dateutil",
    "dotenv": "python-dotenv", "jwt": "pyjwt", "attr": "attrs",
    "serial": "pyserial", "OpenSSL": "pyopenssl", "Crypto": "pycryptodome",
    "google": "google-api-python-client", "docx": "python-docx",
    "pptx": "python-pptx", "fitz": "pymupdf", "psycopg2": "psycopg2-binary",
    "mpl_toolkits": "matplotlib", "pkg_resources": "setuptools",
    "zoneinfo": "backports.zoneinfo", "regex": "regex",
}


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _snippet(source: str, line: int) -> str | None:
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1][:200]
    return None


# ---------------------------------------------------------------- syntax ---


def detect_syntax_problems(
    inventory: RepoInventory,
) -> tuple[list[Problem], set[str]]:
    """Parse every Python file. Returns problems and the set of unparseable files.

    Indentation faults surface here as `IndentationError`/`TabError`, which are
    SyntaxError subclasses; they are reported as indentation problems because
    that is what the user has to fix.
    """
    problems: list[Problem] = []
    broken: set[str] = set()

    for path in inventory.by_suffix(".py", ".pyi"):
        source = _read_text(path)
        rel = inventory.relative(path)
        if source is None:
            continue
        try:
            ast.parse(source, filename=rel)
        except SyntaxError as exc:
            broken.add(rel)
            is_indent = isinstance(exc, (IndentationError, TabError))
            problems.append(
                Problem(
                    file=rel,
                    line=exc.lineno or 1,
                    column=exc.offset or 1,
                    kind=ProblemKind.INDENTATION if is_indent else ProblemKind.SYNTAX,
                    severity=Severity.CRITICAL,
                    code="E999",
                    message=f"{type(exc).__name__}: {exc.msg}",
                    detector="python-ast",
                    snippet=_snippet(source, exc.lineno or 1),
                    auto_fixable=False,
                )
            )
        except ValueError as exc:  # e.g. source containing null bytes
            broken.add(rel)
            problems.append(
                Problem(
                    file=rel, line=1, column=1, kind=ProblemKind.SYNTAX,
                    severity=Severity.CRITICAL, code="E999",
                    message=f"Unparseable source: {exc}", detector="python-ast",
                )
            )
    return problems, broken


# ----------------------------------------------------------- indentation ---

_TAB_INDENT = re.compile(r"^\t+")
_SPACE_INDENT = re.compile(r"^ +")


def detect_indentation_problems(
    inventory: RepoInventory, skip: set[str]
) -> list[Problem]:
    """Flag files that mix tab and space indentation.

    Files that already failed to parse are skipped -- the syntax detector has
    reported the more actionable error for them.
    """
    problems: list[Problem] = []

    for path in inventory.by_suffix(".py"):
        rel = inventory.relative(path)
        if rel in skip:
            continue
        source = _read_text(path)
        if source is None:
            continue

        tab_lines: list[int] = []
        space_lines: list[int] = []
        for number, line in enumerate(source.splitlines(), start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if _TAB_INDENT.match(line):
                tab_lines.append(number)
            elif _SPACE_INDENT.match(line):
                space_lines.append(number)

        if tab_lines and space_lines:
            first = min(tab_lines[0], space_lines[0])
            problems.append(
                Problem(
                    file=rel,
                    line=first,
                    column=1,
                    kind=ProblemKind.INDENTATION,
                    severity=Severity.HIGH,
                    code="MIXED-INDENT",
                    message=(
                        f"File mixes tab and space indentation "
                        f"({len(tab_lines)} tab-indented lines, "
                        f"{len(space_lines)} space-indented). This raises "
                        f"TabError on Python 3."
                    ),
                    detector="indentation",
                    snippet=_snippet(source, first),
                    auto_fixable=True,
                )
            )
    return problems


# --------------------------------------------------------------- imports ---


def _declared_dependencies(root: Path) -> set[str]:
    """Best-effort set of distribution names the project declares."""
    declared: set[str] = set()
    name_pattern = re.compile(r"^([A-Za-z0-9._-]+)")

    for candidate in root.rglob("requirements*.txt"):
        if any(part in {"node_modules", ".venv", "venv"} for part in candidate.parts):
            continue
        text = _read_text(candidate)
        if not text:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-", "git+", "http")):
                continue
            match = name_pattern.match(line)
            if match:
                declared.add(match.group(1).lower().replace("_", "-"))

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
            data = {}
        project = data.get("project", {}) if isinstance(data, dict) else {}
        for entry in project.get("dependencies", []) or []:
            match = name_pattern.match(str(entry))
            if match:
                declared.add(match.group(1).lower().replace("_", "-"))
        for group in (project.get("optional-dependencies") or {}).values():
            for entry in group or []:
                match = name_pattern.match(str(entry))
                if match:
                    declared.add(match.group(1).lower().replace("_", "-"))
        poetry = (
            data.get("tool", {}).get("poetry", {}).get("dependencies", {})
            if isinstance(data, dict)
            else {}
        )
        for entry in poetry:
            declared.add(str(entry).lower().replace("_", "-"))

    return declared


def _local_top_level_modules(root: Path) -> set[str]:
    """Top-level importable names provided by the repository itself."""
    local: set[str] = set()
    skip = {"node_modules", ".git", "__pycache__", ".venv", "venv", "build", "dist"}

    def scan(directory: Path) -> None:
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.name in skip or entry.name.startswith("."):
                continue
            if entry.is_dir() and (entry / "__init__.py").exists():
                local.add(entry.name)
            elif entry.is_file() and entry.suffix == ".py":
                local.add(entry.stem)

    scan(root)
    # Common source roots hold the real package.
    for nested in ("src", "app", "lib", "backend", "server", "python"):
        candidate = root / nested
        if candidate.is_dir():
            scan(candidate)
            local.add(nested)
    return local


def detect_import_problems(
    inventory: RepoInventory, skip: set[str]
) -> list[Problem]:
    """Find imports that cannot resolve.

    Relative imports are checked against the actual file layout, which is a
    high-confidence signal. Absolute third-party imports are only flagged when
    they are neither installed, nor declared as a dependency, nor provided by
    the repo -- and are reported at a lower severity because the environment
    the code finally runs in may legitimately differ from this one.
    """
    problems: list[Problem] = []
    root = inventory.root
    declared = _declared_dependencies(root)
    local_modules = _local_top_level_modules(root)
    stdlib = set(sys.stdlib_module_names)

    for path in inventory.by_suffix(".py"):
        rel = inventory.relative(path)
        if rel in skip:
            continue
        source = _read_text(path)
        if source is None:
            continue
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                problem = _check_relative_import(node, path, root, rel, source)
                if problem:
                    problems.append(problem)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom):
                    names = [node.module.split(".")[0]] if node.module else []
                else:
                    names = [alias.name.split(".")[0] for alias in node.names]
                for name in names:
                    if not name or name in stdlib or name in local_modules:
                        continue
                    distribution = IMPORT_TO_DISTRIBUTION.get(
                        name, name.lower().replace("_", "-")
                    )
                    if distribution in declared or name.lower() in declared:
                        continue
                    if _is_installed(name):
                        continue
                    problems.append(
                        Problem(
                            file=rel,
                            line=node.lineno,
                            column=node.col_offset + 1,
                            kind=ProblemKind.IMPORT,
                            severity=Severity.MEDIUM,
                            code="UNDECLARED-IMPORT",
                            message=(
                                f"Module '{name}' is imported but is not in the "
                                f"standard library, not provided by this "
                                f"repository, and not declared as a dependency "
                                f"(expected distribution '{distribution}')."
                            ),
                            detector="import-graph",
                            snippet=_snippet(source, node.lineno),
                            auto_fixable=False,
                        )
                    )
    return problems


def _check_relative_import(
    node: ast.ImportFrom, path: Path, root: Path, rel: str, source: str
) -> Problem | None:
    """Resolve `from .x import y` against the real directory layout."""
    package_dir = path.parent
    for _ in range(node.level - 1):
        package_dir = package_dir.parent

    try:
        package_dir.relative_to(root)
    except ValueError:
        return Problem(
            file=rel, line=node.lineno, column=node.col_offset + 1,
            kind=ProblemKind.IMPORT, severity=Severity.HIGH,
            code="RELATIVE-IMPORT-ESCAPE",
            message=(
                f"Relative import climbs {node.level} levels, past the "
                f"repository root."
            ),
            detector="import-graph", snippet=_snippet(source, node.lineno),
        )

    if not node.module:
        return None

    target = package_dir
    for part in node.module.split("."):
        target = target / part

    if target.with_suffix(".py").is_file() or (target / "__init__.py").is_file():
        return None
    if target.is_dir():
        return None

    return Problem(
        file=rel,
        line=node.lineno,
        column=node.col_offset + 1,
        kind=ProblemKind.IMPORT,
        severity=Severity.HIGH,
        code="UNRESOLVED-RELATIVE-IMPORT",
        message=(
            f"Relative import '{'.' * node.level}{node.module}' does not "
            f"resolve: no module or package found at "
            f"'{target.relative_to(root) if target.is_relative_to(root) else target}'."
        ),
        detector="import-graph",
        snippet=_snippet(source, node.lineno),
        auto_fixable=False,
    )


_INSTALLED_CACHE: dict[str, bool] = {}


def _is_installed(module: str) -> bool:
    if module in _INSTALLED_CACHE:
        return _INSTALLED_CACHE[module]
    try:
        import importlib.util

        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError, AttributeError):
        found = False
    _INSTALLED_CACHE[module] = found
    return found
