"""Thin, typed wrapper over the GitHub REST API.

Used by both the repository layer (clone / commit / push without a git binary)
and the CI/CD monitor (workflow-run telemetry, job logs, rerun controls).
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from .redaction import register_secret, scrub

API_ROOT = "https://api.github.com"
USER_AGENT = "autonomous-cicd-healing-agent/1.0"


class GitHubError(RuntimeError):
    """A GitHub API call failed."""

    def __init__(self, message: str, status: int | None = None,
                 hint: str | None = None) -> None:
        super().__init__(scrub(message))
        self.status = status
        self.hint = hint


@dataclass
class RateLimit:
    limit: int = 0
    remaining: int = 0
    reset_at: float = 0.0

    @property
    def exhausted(self) -> bool:
        return self.limit > 0 and self.remaining == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
            "seconds_until_reset": max(0, int(self.reset_at - time.time()))
            if self.reset_at
            else 0,
        }


class GitHubClient:
    """Synchronous GitHub API client with retry and rate-limit awareness."""

    def __init__(
        self,
        token: str | None = None,
        api_root: str = API_ROOT,
        timeout: float = 30.0,
    ) -> None:
        self.token = token
        self.api_root = api_root.rstrip("/")
        register_secret(token)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            headers=headers, timeout=timeout, follow_redirects=True
        )
        self.rate_limit = RateLimit()

    # ------------------------------------------------------------------ core
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _record_rate_limit(self, response: httpx.Response) -> None:
        headers = response.headers
        try:
            if "x-ratelimit-limit" in headers:
                self.rate_limit = RateLimit(
                    limit=int(headers.get("x-ratelimit-limit", 0)),
                    remaining=int(headers.get("x-ratelimit-remaining", 0)),
                    reset_at=float(headers.get("x-ratelimit-reset", 0)),
                )
        except (TypeError, ValueError):  # pragma: no cover - defensive
            pass

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
        raw: bool = False,
        retries: int = 3,
        expected: Iterable[int] = (200, 201, 204),
    ) -> Any:
        url = path if path.startswith("http") else f"{self.api_root}{path}"
        last_error: Exception | None = None

        for attempt in range(retries):
            try:
                response = self._client.request(
                    method, url, json=json_body, params=params
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == retries - 1:
                    raise GitHubError(
                        f"Network failure contacting GitHub: {exc}",
                        hint="Check outbound network access and any proxy settings.",
                    ) from exc
                time.sleep(2**attempt)
                continue

            self._record_rate_limit(response)

            # Secondary rate limits and transient server errors are retryable.
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt < retries - 1:
                    retry_after = response.headers.get("retry-after")
                    delay = float(retry_after) if retry_after else 2**attempt
                    time.sleep(min(delay, 30.0))
                    continue

            if response.status_code in expected:
                if raw:
                    return response.content
                if response.status_code == 204 or not response.content:
                    return None
                return response.json()

            raise GitHubError(
                self._describe_error(response),
                status=response.status_code,
                hint=self._hint_for(response.status_code),
            )

        raise GitHubError(  # pragma: no cover - loop always returns or raises
            f"GitHub request failed after {retries} attempts: {last_error}"
        )

    @staticmethod
    def _describe_error(response: httpx.Response) -> str:
        try:
            body = response.json()
            message = body.get("message", response.text[:300])
            errors = body.get("errors")
            if errors:
                message = f"{message} ({errors})"
        except Exception:
            message = response.text[:300]
        return f"GitHub API {response.status_code} on {response.request.url.path}: {message}"

    @staticmethod
    def _hint_for(status: int) -> str | None:
        return {
            401: "The GitHub token is invalid or expired. Generate a new token "
                 "with 'repo' scope (classic) or Contents: read/write (fine-grained).",
            403: "The token lacks permission, or a rate limit was hit. Confirm the "
                 "token can write to this repository.",
            404: "Repository or ref not found. For a private repository the token "
                 "must have access to it.",
            409: "The repository is empty, or the ref already moved. ",
            422: "GitHub rejected the payload - the branch may already exist.",
        }.get(status)

    # ------------------------------------------------------------ repository
    def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return self.request("GET", f"/repos/{owner}/{repo}")

    def get_authenticated_user(self) -> dict[str, Any] | None:
        try:
            return self.request("GET", "/user")
        except GitHubError:
            return None

    def list_branches(self, owner: str, repo: str, per_page: int = 100) -> list[dict]:
        return self.request(
            "GET", f"/repos/{owner}/{repo}/branches", params={"per_page": per_page}
        ) or []

    def get_ref(self, owner: str, repo: str, ref: str) -> dict[str, Any]:
        return self.request("GET", f"/repos/{owner}/{repo}/git/ref/{ref}")

    def create_ref(self, owner: str, repo: str, ref: str, sha: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json_body={"ref": ref, "sha": sha},
        )

    def update_ref(
        self, owner: str, repo: str, ref: str, sha: str, force: bool = False
    ) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/repos/{owner}/{repo}/git/refs/{ref}",
            json_body={"sha": sha, "force": force},
        )

    def get_commit(self, owner: str, repo: str, sha: str) -> dict[str, Any]:
        return self.request("GET", f"/repos/{owner}/{repo}/commits/{sha}")

    def create_blob(self, owner: str, repo: str, content_b64: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/repos/{owner}/{repo}/git/blobs",
            json_body={"content": content_b64, "encoding": "base64"},
        )

    def create_tree(
        self, owner: str, repo: str, base_tree: str, tree: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/repos/{owner}/{repo}/git/trees",
            json_body={"base_tree": base_tree, "tree": tree},
        )

    def create_commit(
        self,
        owner: str,
        repo: str,
        message: str,
        tree: str,
        parents: list[str],
        author: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"message": message, "tree": tree, "parents": parents}
        if author:
            body["author"] = author
            body["committer"] = author
        return self.request(
            "POST", f"/repos/{owner}/{repo}/git/commits", json_body=body
        )

    def download_tarball(self, owner: str, repo: str, ref: str) -> bytes:
        return self.request(
            "GET", f"/repos/{owner}/{repo}/tarball/{ref}", raw=True, expected=(200,)
        )

    # -------------------------------------------------------------- workflows
    def list_workflow_runs(
        self, owner: str, repo: str, per_page: int = 30, branch: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"per_page": per_page}
        if branch:
            params["branch"] = branch
        return self.request(
            "GET", f"/repos/{owner}/{repo}/actions/runs", params=params
        ) or {}

    def list_run_jobs(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
            params={"per_page": 100},
        ) or {}

    def rerun_failed_jobs(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        return self.request(
            "POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs"
        ) or {}

    def list_commits(
        self, owner: str, repo: str, per_page: int = 30, path: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": per_page}
        if path:
            params["path"] = path
        return self.request(
            "GET", f"/repos/{owner}/{repo}/commits", params=params
        ) or []

    def list_open_pulls(self, owner: str, repo: str) -> list[dict[str, Any]]:
        return self.request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "open", "per_page": 100},
        ) or []

    def create_pull_request(
        self, owner: str, repo: str, title: str, head: str, base: str, body: str
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json_body={"title": title, "head": head, "base": base, "body": body},
        )
