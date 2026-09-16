"""Repository inventory: which files exist, and what languages they are."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..repo.base import iter_repo_files

LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML",
    ".md": "Markdown", ".rst": "reStructuredText",
    ".sh": "Shell", ".bash": "Shell",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".java": "Java",
    ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++",
    ".css": "CSS", ".scss": "CSS", ".html": "HTML",
    ".sql": "SQL", ".dockerfile": "Docker",
}


@dataclass
class RepoInventory:
    root: Path
    files: list[Path] = field(default_factory=list)
    languages: dict[str, int] = field(default_factory=dict)

    def by_suffix(self, *suffixes: str) -> list[Path]:
        wanted = {s.lower() for s in suffixes}
        return [f for f in self.files if f.suffix.lower() in wanted]

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root)).replace("\\", "/")
        except ValueError:  # pragma: no cover - defensive
            return str(path)

    @property
    def primary_language(self) -> str:
        if not self.languages:
            return "unknown"
        return max(self.languages.items(), key=lambda kv: kv[1])[0]


def build_inventory(root: Path, max_files: int = 4000) -> RepoInventory:
    files = iter_repo_files(root, max_files=max_files)
    languages: dict[str, int] = {}
    for path in files:
        name = path.name.lower()
        if name in {"dockerfile", "containerfile"}:
            language = "Docker"
        elif name == "makefile":
            language = "Make"
        else:
            language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
        if language:
            languages[language] = languages.get(language, 0) + 1
    return RepoInventory(root=root, files=files, languages=languages)
