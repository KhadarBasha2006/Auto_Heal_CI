"""Git-CLI-backed repository access.

The GitHub token is passed to git through `GIT_CONFIG_*` environment variables
rather than being embedded in the remote URL or in a `-c` argument. That keeps
it out of `.git/config`, out of the process argument list, and therefore out of
anything that later reads either.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

from ..redaction import scrub
from .base import (
    PushResult,
    RepoBackend,
    RepoCheckout,
    fingerprint_tree,
)


class GitCommandError(RuntimeError):
    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        redacted = scrub(stderr.strip())
        super().__init__(
            f"git {' '.join(command[1:3])} failed (exit {returncode}): {redacted}"
        )
        self.returncode = returncode
        self.stderr = redacted


class GitCliBackend(RepoBackend):
    name = "git-cli"

    AUTH_FAILURE_MARKERS = (
        "could not read Username",
        "Authentication failed",
        "Invalid username or password",
        "terminal prompts disabled",
        "remote: Repository not found",
        "403",
    )

    def __init__(self, token: str | None, timeout: float = 300.0) -> None:
        self.token = token
        self.timeout = timeout
        self._cloned_anonymously = False

    @classmethod
    def _is_auth_failure(cls, message: str) -> bool:
        return any(marker in message for marker in cls.AUTH_FAILURE_MARKERS)

    # ------------------------------------------------------------------ util
    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Never let git block waiting for interactive credentials.
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "never"
        # GIT_CONFIG_NOSYSTEM is deliberately NOT set: proxied CI images
        # and enterprise runners keep credential helpers and URL rewrites
        # in the system config, and suppressing it breaks their auth.
        if self.token:
            # Supplying the auth header via GIT_CONFIG_* keeps the credential
            # out of argv and out of the repository's own config file.
            basic = base64.b64encode(
                f"x-access-token:{self.token}".encode()
            ).decode()
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
            env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {basic}"
        return env

    def _run(
        self, args: list[str], cwd: Path | None = None, check: bool = True
    ) -> subprocess.CompletedProcess:
        command = ["git", *args]
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if check and result.returncode != 0:
            raise GitCommandError(command, result.returncode, result.stderr)
        return result

    # --------------------------------------------------------------- prepare
    def prepare(
        self,
        owner: str,
        repo: str,
        workspace: Path,
        base_branch: str | None = None,
    ) -> RepoCheckout:
        target = workspace / f"{owner}__{repo}"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)

        url = f"https://github.com/{owner}/{repo}.git"
        clone_args = ["clone", "--depth", "50", "--no-tags"]
        if base_branch:
            clone_args += ["--branch", base_branch]
        clone_args += [url, str(target)]

        try:
            self._run(clone_args)
        except GitCommandError as exc:
            message = str(exc)
            if base_branch and "not found in upstream" in message:
                # Requested base does not exist - fall back to the default branch.
                self._run(["clone", "--depth", "50", "--no-tags", url, str(target)])
            elif self.token and self._is_auth_failure(message):
                # A token that cannot see this repository should not stop us
                # analysing a public one. Retry without credentials; push will
                # still fail later if the token really is inadequate, and that
                # error is reported at the point it actually matters.
                shutil.rmtree(target, ignore_errors=True)
                anonymous = GitCliBackend(token=None, timeout=self.timeout)
                anonymous._run(clone_args)
                self._cloned_anonymously = True
            else:
                raise

        default_branch = self._detect_default_branch(target)
        head_branch = self._run(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=target
        ).stdout.strip()
        base_sha = self._run(["rev-parse", "HEAD"], cwd=target).stdout.strip()

        return RepoCheckout(
            path=target,
            owner=owner,
            repo=repo,
            default_branch=default_branch,
            base_branch=base_branch or head_branch or default_branch,
            base_sha=base_sha,
            backend=self.name,
            fingerprint=fingerprint_tree(target),
        )

    def _detect_default_branch(self, target: Path) -> str:
        result = self._run(
            ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=target,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("/", 1)[-1]
        head = self._run(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=target, check=False
        )
        return head.stdout.strip() or "main"

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

        repo_path = checkout.path
        # Create the branch from the checked-out base.
        self._run(["checkout", "-B", branch], cwd=repo_path)

        for rel in changed_files:
            self._run(["add", "--", rel], cwd=repo_path)

        staged = self._run(
            ["diff", "--cached", "--name-only"], cwd=repo_path
        ).stdout.strip()
        if not staged:
            raise ValueError("Nothing staged after adding changed files")

        env_overrides = {
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }
        env = self._env()
        env.update(env_overrides)
        commit = subprocess.run(
            ["git", "commit", "-m", commit_message, "--no-verify"],
            cwd=str(repo_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if commit.returncode != 0:
            raise GitCommandError(
                ["git", "commit"], commit.returncode, commit.stderr
            )

        commit_sha = self._run(["rev-parse", "HEAD"], cwd=repo_path).stdout.strip()
        self._run(
            ["push", "--set-upstream", "origin", f"{branch}:refs/heads/{branch}"],
            cwd=repo_path,
        )

        return PushResult(
            branch=branch,
            commit_sha=commit_sha,
            branch_url=self.branch_url(checkout.owner, checkout.repo, branch),
            compare_url=self.compare_url(
                checkout.owner, checkout.repo, checkout.base_branch, branch
            ),
            files_committed=sorted(staged.splitlines()),
        )
