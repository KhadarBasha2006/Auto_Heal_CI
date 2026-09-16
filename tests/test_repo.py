"""Repository layer: URL parsing and safe archive extraction."""

import io
import os
import tarfile
from pathlib import Path

import pytest
from healing_agent.models import AnalyzeRequest
from healing_agent.repo.base import iter_repo_files
from healing_agent.repo.githubapi import GitHubApiBackend
from pydantic import ValidationError


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/octocat/Hello-World", ("octocat", "Hello-World")),
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("http://github.com/owner/repo/", ("owner", "repo")),
        ("git@github.com:owner/repo.git", ("owner", "repo")),
        ("github.com/owner/repo", ("owner", "repo")),
        # A trailing dash is legal in a repository name.
        ("https://github.com/Ayaan48/git-reverse-", ("Ayaan48", "git-reverse-")),
        ("https://www.github.com/o/r", ("o", "r")),
    ],
)
def test_repo_url_parsing(url, expected):
    request = AnalyzeRequest(repo_url=url, author_name="A", branch_name="b")
    assert request.owner_repo() == expected


@pytest.mark.parametrize(
    "url", ["https://gitlab.com/a/b", "not a url", "https://github.com/only-owner", ""]
)
def test_invalid_repo_urls_are_rejected(url):
    with pytest.raises(ValidationError):
        AnalyzeRequest(repo_url=url, author_name="A", branch_name="b")


def _archive(members: list[tuple[str, bytes, int]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data, mode in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("repo-sha/evil-link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    return buffer.getvalue()


def test_extraction_strips_the_top_level_directory(tmp_path: Path):
    GitHubApiBackend._extract(
        _archive([("repo-sha/src/app.py", b"print(1)\n", 0o644)]), tmp_path
    )
    assert (tmp_path / "src" / "app.py").read_text() == "print(1)\n"


def test_extraction_preserves_the_executable_bit(tmp_path: Path):
    GitHubApiBackend._extract(
        _archive([("repo-sha/run.sh", b"#!/bin/sh\n", 0o755)]), tmp_path
    )
    assert os.access(tmp_path / "run.sh", os.X_OK)


def test_extraction_rejects_hostile_members(tmp_path: Path):
    """Path traversal, absolute paths, and symlinks must never be written."""
    GitHubApiBackend._extract(
        _archive([
            ("repo-sha/ok.py", b"x = 1\n", 0o644),
            ("repo-sha/../../escape.txt", b"ESCAPED", 0o644),
            ("/absolute.txt", b"ABS", 0o644),
            ("repo-sha/../sneaky.txt", b"SNEAK", 0o644),
        ]),
        tmp_path,
    )
    written = {p.name for p in tmp_path.rglob("*") if p.is_file()}
    assert written == {"ok.py"}
    assert not (tmp_path.parent / "escape.txt").exists()
    assert not (tmp_path / "evil-link").exists()


def test_walker_skips_vendor_directories_but_keeps_dot_github(tmp_path: Path):
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: CI\n")
    (tmp_path / "app.py").write_text("x = 1\n")

    found = {str(p.relative_to(tmp_path)) for p in iter_repo_files(tmp_path)}
    assert "app.py" in found
    assert ".github/workflows/ci.yml" in found
    assert not any(f.startswith("node_modules") for f in found)
    assert not any(f.startswith(".git/") for f in found)
