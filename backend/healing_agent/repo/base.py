"""Repository access abstraction.

Two interchangeable backends implement this interface:

* `GitCliBackend`   - shells out to `git`. Full fidelity, used wherever a git
                      binary exists (local dev, Docker, most PaaS hosts).
* `GitHubApiBackend`- downloads a tarball and writes the commit through the
                      GitHub Git Data API. Needed on serverless runtimes such
                      as Vercel's Python functions, which ship no git binary.

Change detection is backend-independent: the checkout is fingerprinted on
arrival and re-fingerprinted at publish time, so whatever edited the tree gets
picked up without either backend needing its own diff logic.
"""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Directories that are never scanned, fingerprinted, or committed.
IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".nuxt", "target", "vendor", ".tox", ".gradle", ".idea",
    ".vscode", "site-packages", ".terraform", "coverage", ".cache",
    ".svelte-kit", "out", ".turbo", ".parcel-cache", "bower_components",
}

# Binary-ish extensions we never read as text.
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
    ".pdf", ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".rar",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".class",
    ".pyc", ".pyo", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".webm", ".jar", ".wasm",
    ".db", ".sqlite", ".sqlite3", ".lock",
}


@dataclass
class RepoCheckout:
    """A materialised working tree plus the refs it came from."""

    path: Path
    owner: str
    repo: str
    default_branch: str
    base_branch: str
    base_sha: str
    backend: str
    fingerprint: dict[str, str] = field(default_factory=dict)
    private: bool = False

    def relative(self, path: Path) -> str:
        return str(path.relative_to(self.path)).replace(os.sep, "/")


@dataclass
class PushResult:
    branch: str
    commit_sha: str
    branch_url: str
    compare_url: str
    files_committed: list[str]
    created_branch: bool = True


def iter_repo_files(root: Path, max_files: int = 10000) -> list[Path]:
    """Walk the checkout, skipping vendor/build directories and binaries."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Exclude the git database itself, but never `.github` -- workflow
        # files under it are central to what this agent inspects.
        dirnames[:] = sorted(
            d for d in dirnames if d not in IGNORED_DIRS and d != ".git"
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            found.append(path)
            if len(found) >= max_files:
                return found
    return found


def fingerprint_tree(root: Path, max_files: int = 10000) -> dict[str, str]:
    """Map repo-relative path -> content hash for every tracked text file."""
    prints: dict[str, str] = {}
    for path in iter_repo_files(root, max_files=max_files):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        prints[str(path.relative_to(root)).replace(os.sep, "/")] = digest
    return prints


def changed_since(root: Path, baseline: dict[str, str],
                  max_files: int = 10000) -> list[str]:
    """Return repo-relative paths that were added or modified since baseline."""
    current = fingerprint_tree(root, max_files=max_files)
    changed = [
        rel for rel, digest in current.items() if baseline.get(rel) != digest
    ]
    return sorted(changed)


class RepoBackend(ABC):
    """Fetch a repository, then publish edits back to a new branch."""

    name: str = "base"

    @abstractmethod
    def prepare(
        self,
        owner: str,
        repo: str,
        workspace: Path,
        base_branch: str | None = None,
    ) -> RepoCheckout:
        """Materialise the repository into `workspace` and describe it."""

    @abstractmethod
    def publish(
        self,
        checkout: RepoCheckout,
        branch: str,
        commit_message: str,
        author_name: str,
        author_email: str,
        changed_files: list[str],
    ) -> PushResult:
        """Commit `changed_files` onto a new `branch` and push it."""

    @staticmethod
    def branch_url(owner: str, repo: str, branch: str) -> str:
        from urllib.parse import quote
        return f"https://github.com/{owner}/{repo}/tree/{quote(branch, safe='/')}"

    @staticmethod
    def compare_url(owner: str, repo: str, base: str, branch: str) -> str:
        from urllib.parse import quote
        return (
            f"https://github.com/{owner}/{repo}/compare/"
            f"{quote(base, safe='/')}...{quote(branch, safe='/')}?expand=1"
        )

    def describe(self) -> dict[str, Any]:
        return {"backend": self.name}
