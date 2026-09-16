"""End-to-end pipeline over a local checkout.

The repository backend is stubbed so the run is deterministic and offline; every
other stage -- scan, diagnose, heal, validate, score, report -- is the real one.
"""

import asyncio
import shutil
from pathlib import Path

from healing_agent import pipeline as pipeline_module
from healing_agent.config import Settings
from healing_agent.jobstore import JobStore
from healing_agent.models import AnalyzeRequest, JobStatus
from healing_agent.repo.base import (
    PushResult,
    RepoBackend,
    RepoCheckout,
    fingerprint_tree,
)


def _stub_backend(source: Path):
    class LocalBackend(RepoBackend):
        name = "local-stub"

        def prepare(self, owner, repo, workspace, base_branch=None):
            target = workspace / f"{owner}__{repo}"
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(source, target)
            return RepoCheckout(
                path=target, owner=owner, repo=repo, default_branch="main",
                base_branch=base_branch or "main", base_sha="a" * 40,
                backend=self.name, fingerprint=fingerprint_tree(target),
            )

        def publish(self, checkout, branch, message, author, email, changed):
            return PushResult(
                branch=branch, commit_sha="b" * 40,
                branch_url=f"https://github.com/{checkout.owner}/{checkout.repo}/tree/{branch}",
                compare_url="https://github.com/o/r/compare/main...x",
                files_committed=changed,
            )

    return LocalBackend()


def _run(source: Path, tmp_path: Path, monkeypatch, **overrides):
    monkeypatch.setattr(
        pipeline_module, "select_backend",
        lambda settings, client, token: _stub_backend(source),
    )
    settings = Settings(
        anthropic_api_key=None, workspace_root=tmp_path / "ws", max_rounds=2
    )
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    job = JobStore().create(
        repo_url="https://github.com/demo/repo", owner="demo", repo="repo",
        author_name="Tester", branch_name="heal/fixes",
    )
    fields = {
        "repo_url": "https://github.com/demo/repo",
        "author_name": "Tester",
        "branch_name": "heal/fixes",
        "push": True,
        "run_tests": False,
        "use_ai": False,
        "github_token": "ghp_" + "x" * 36,
    }
    fields.update(overrides)
    request = AnalyzeRequest(**fields)

    async def drive():
        job.bind_loop(asyncio.get_running_loop())
        await asyncio.to_thread(pipeline_module.run_pipeline, job, request, settings)

    asyncio.run(drive())
    return job


def test_broken_repository_is_analysed_repaired_and_published(
    broken_repo, tmp_path, monkeypatch
):
    job = _run(broken_repo, tmp_path, monkeypatch)
    snapshot = job.snapshot()

    assert snapshot["problems_found"] > 0, "defects must be detected"
    assert snapshot["fixes_applied"] > 0, "repairs must be applied"
    assert snapshot["validations"], "validation must run"
    assert snapshot["branch_url"].endswith("heal/fixes")
    assert snapshot["score"]["total"] >= 0
    assert snapshot["incident_report"], "a post-incident report must be produced"
    assert job.status in {JobStatus.SUCCEEDED, JobStatus.PARTIAL}


def test_repairs_actually_reduce_the_problem_count(
    broken_repo, tmp_path, monkeypatch
):
    job = _run(broken_repo, tmp_path, monkeypatch)
    metrics = job.snapshot()["score"]["metrics"]
    assert metrics["problems_after"] < metrics["problems_before"]


def test_clean_repository_still_gets_validated(clean_repo, tmp_path, monkeypatch):
    """'Nothing to fix' must be distinguishable from 'never checked'."""
    job = _run(clean_repo, tmp_path, monkeypatch)
    snapshot = job.snapshot()

    assert snapshot["problems_found"] == 0
    assert snapshot["validations"], "a clean repo must still be proven green"
    assert snapshot["validation_passed"] is True
    assert snapshot["score"]["total"] == 100
    assert job.status is JobStatus.SUCCEEDED


def test_no_token_still_analyses_but_reports_it_cannot_push(
    broken_repo, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        pipeline_module, "select_backend",
        lambda settings, client, token: _stub_backend(broken_repo),
    )
    settings = Settings(
        anthropic_api_key=None, workspace_root=tmp_path / "ws2", max_rounds=1,
        fallback_github_token=None,
    )
    (tmp_path / "ws2").mkdir(parents=True, exist_ok=True)
    job = JobStore().create(
        repo_url="https://github.com/demo/repo", owner="demo", repo="repo",
        author_name="Tester", branch_name="heal/fixes",
    )
    request = AnalyzeRequest(
        repo_url="https://github.com/demo/repo", author_name="Tester",
        branch_name="heal/fixes", push=True, run_tests=False, use_ai=False,
        github_token=None,
    )

    async def drive():
        job.bind_loop(asyncio.get_running_loop())
        await asyncio.to_thread(pipeline_module.run_pipeline, job, request, settings)

    asyncio.run(drive())
    snapshot = job.snapshot()
    assert snapshot["problems_found"] > 0
    assert snapshot["branch_url"] is None
    assert any("token" in entry["message"].lower() for entry in snapshot["logs"])


def test_dry_run_does_not_publish(broken_repo, tmp_path, monkeypatch):
    job = _run(broken_repo, tmp_path, monkeypatch, push=False)
    assert job.snapshot()["branch_url"] is None
