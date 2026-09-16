"""Repository access with no git binary, via the GitHub REST API.

Serverless Python runtimes (Vercel functions among them) do not ship `git`.
This backend downloads a source tarball to materialise the tree, then writes
the resulting commit through the Git Data API: blob -> tree -> commit -> ref.
That is exactly what `git push` does, expressed over HTTPS.
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import tarfile
from pathlib import Path

from ..github import GitHubClient, GitHubError
from .base import PushResult, RepoBackend, RepoCheckout, fingerprint_tree

# Guard against a hostile or accidentally enormous archive.
MAX_TARBALL_BYTES = 250 * 1024 * 1024
MAX_MEMBER_BYTES = 25 * 1024 * 1024


class GitHubApiBackend(RepoBackend):
    name = "github-api"

    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    # --------------------------------------------------------------- prepare
    def prepare(
        self,
        owner: str,
        repo: str,
        workspace: Path,
        base_branch: str | None = None,
    ) -> RepoCheckout:
        info = self.client.get_repo(owner, repo)
        default_branch = info.get("default_branch") or "main"
        ref = base_branch or default_branch

        try:
            head = self.client.get_ref(owner, repo, f"heads/{ref}")
            base_sha = head["object"]["sha"]
        except GitHubError:
            if base_branch:
                # Requested base is missing; fall back to the default branch.
                ref = default_branch
                head = self.client.get_ref(owner, repo, f"heads/{ref}")
                base_sha = head["object"]["sha"]
            else:
                raise

        target = workspace / f"{owner}__{repo}"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)

        archive = self.client.download_tarball(owner, repo, base_sha)
        if len(archive) > MAX_TARBALL_BYTES:
            raise GitHubError(
                f"Repository archive is {len(archive) // (1024 * 1024)} MB, "
                f"above the {MAX_TARBALL_BYTES // (1024 * 1024)} MB limit."
            )
        self._extract(archive, target)

        return RepoCheckout(
            path=target,
            owner=owner,
            repo=repo,
            default_branch=default_branch,
            base_branch=ref,
            base_sha=base_sha,
            backend=self.name,
            fingerprint=fingerprint_tree(target),
            private=bool(info.get("private")),
        )

    @staticmethod
    def _extract(archive: bytes, target: Path) -> None:
        """Extract the tarball, stripping GitHub's top-level directory.

        Members are validated against path traversal and symlink escapes before
        anything is written to disk.
        """
        target_root = target.resolve()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.issym() or member.islnk():
                    continue
                if not (member.isfile() or member.isdir()):
                    continue
                if member.size > MAX_MEMBER_BYTES:
                    continue

                # Reject absolute members outright rather than silently
                # rewriting them into repo-relative paths.
                name = member.name.replace("\\", "/")
                if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
                    continue
                parts = Path(name).parts
                if len(parts) <= 1 or ".." in parts:
                    continue
                # GitHub archives nest everything under a single top-level
                # directory ("<repo>-<sha>/"); strip exactly that one level.
                relative = Path(*parts[1:])
                if relative.is_absolute():
                    continue

                destination = (target_root / relative).resolve()
                if not str(destination).startswith(str(target_root) + os.sep):
                    continue

                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue

                destination.parent.mkdir(parents=True, exist_ok=True)
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                with open(destination, "wb") as handle:
                    shutil.copyfileobj(extracted, handle)
                if member.mode & 0o111:
                    destination.chmod(0o755)

    # --------------------------------------------------------------- publish
    def publish(
        self,
        checkout: RepoCheckout,
        branch: str,
        commit_message: str,
        author_name: str,
        author_email: str,
        changed_files: list[str],
    ) -> PushResult:
        if not changed_files:
            raise ValueError("No changed files to publish")

        owner, repo = checkout.owner, checkout.repo
        base_commit = self.client.get_commit(owner, repo, checkout.base_sha)
        base_tree_sha = base_commit["commit"]["tree"]["sha"]

        tree_entries: list[dict[str, object]] = []
        for rel in changed_files:
            absolute = checkout.path / rel
            if not absolute.is_file():
                continue
            content = absolute.read_bytes()
            blob = self.client.create_blob(
                owner, repo, base64.b64encode(content).decode("ascii")
            )
            mode = "100755" if os.access(absolute, os.X_OK) else "100644"
            tree_entries.append(
                {"path": rel, "mode": mode, "type": "blob", "sha": blob["sha"]}
            )

        if not tree_entries:
            raise ValueError("No readable changed files to commit")

        new_tree = self.client.create_tree(owner, repo, base_tree_sha, tree_entries)
        author = {"name": author_name, "email": author_email}
        commit = self.client.create_commit(
            owner,
            repo,
            commit_message,
            new_tree["sha"],
            [checkout.base_sha],
            author=author,
        )
        commit_sha = commit["sha"]

        created = True
        try:
            self.client.create_ref(owner, repo, f"refs/heads/{branch}", commit_sha)
        except GitHubError as exc:
            if exc.status == 422:
                # Branch already exists - move it forward instead.
                self.client.update_ref(
                    owner, repo, f"heads/{branch}", commit_sha, force=True
                )
                created = False
            else:
                raise

        return PushResult(
            branch=branch,
            commit_sha=commit_sha,
            branch_url=self.branch_url(owner, repo, branch),
            compare_url=self.compare_url(owner, repo, checkout.base_branch, branch),
            files_committed=[str(entry["path"]) for entry in tree_entries],
            created_branch=created,
        )
