"""Repository access layer with automatic backend selection."""

from __future__ import annotations

from ..config import Settings
from ..github import GitHubClient
from .base import (
    IGNORED_DIRS,
    PushResult,
    RepoBackend,
    RepoCheckout,
    changed_since,
    fingerprint_tree,
    iter_repo_files,
)
from .gitcli import GitCliBackend, GitCommandError
from .githubapi import GitHubApiBackend

__all__ = [
    "IGNORED_DIRS",
    "GitCliBackend",
    "GitCommandError",
    "GitHubApiBackend",
    "PushResult",
    "RepoBackend",
    "RepoCheckout",
    "changed_since",
    "fingerprint_tree",
    "iter_repo_files",
    "select_backend",
]


def select_backend(
    settings: Settings, client: GitHubClient, token: str | None
) -> RepoBackend:
    """Pick the best available backend.

    Prefers the git CLI when present because it handles large repositories and
    line-ending nuances natively; falls back to the pure-HTTP backend on hosts
    without a git binary (serverless functions).
    """
    if settings.git_cli_available:
        return GitCliBackend(token=token)
    return GitHubApiBackend(client=client)
