"""Pipeline telemetry from the GitHub Actions API.

Turns raw workflow-run records into the health signals the diagnosis engine
needs: failure rate, queue latency, stuck jobs, and the actual error text of
failing steps.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..github import GitHubClient, GitHubError

# A run waiting longer than this to be assigned a runner is a capacity signal.
QUEUE_WARNING_SECONDS = 120
QUEUE_CRITICAL_SECONDS = 600


def _parse_time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class JobFailure:
    """A failing job, with the step that failed and any error text."""

    run_id: int
    job_name: str
    workflow_name: str
    step_name: str | None
    conclusion: str
    started_at: str | None
    log_excerpt: str = ""
    runner_labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_name": self.job_name,
            "workflow_name": self.workflow_name,
            "step_name": self.step_name,
            "conclusion": self.conclusion,
            "started_at": self.started_at,
            "log_excerpt": self.log_excerpt,
            "runner_labels": self.runner_labels,
        }


@dataclass
class PipelineTelemetry:
    """Aggregated health of a repository's Actions pipeline."""

    available: bool = False
    error: str | None = None
    total_runs: int = 0
    failed_runs: int = 0
    successful_runs: int = 0
    cancelled_runs: int = 0
    in_progress_runs: int = 0
    queued_runs: int = 0
    stuck_queued_runs: int = 0
    failure_rate: float = 0.0
    queue_seconds_p50: float = 0.0
    queue_seconds_p95: float = 0.0
    max_queue_seconds: float = 0.0
    consecutive_failures: int = 0
    distinct_failing_workflows: int = 0
    failing_workflows: list[str] = field(default_factory=list)
    job_failures: list[JobFailure] = field(default_factory=list)
    recent_runs: list[dict[str, Any]] = field(default_factory=list)
    has_workflows: bool = False

    @property
    def queue_pressure(self) -> str:
        if self.max_queue_seconds >= QUEUE_CRITICAL_SECONDS:
            return "critical"
        if self.queue_seconds_p95 >= QUEUE_WARNING_SECONDS:
            return "elevated"
        return "normal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "error": self.error,
            "has_workflows": self.has_workflows,
            "total_runs": self.total_runs,
            "failed_runs": self.failed_runs,
            "successful_runs": self.successful_runs,
            "cancelled_runs": self.cancelled_runs,
            "in_progress_runs": self.in_progress_runs,
            "queued_runs": self.queued_runs,
            "stuck_queued_runs": self.stuck_queued_runs,
            "failure_rate": round(self.failure_rate, 3),
            "queue_seconds_p50": round(self.queue_seconds_p50, 1),
            "queue_seconds_p95": round(self.queue_seconds_p95, 1),
            "max_queue_seconds": round(self.max_queue_seconds, 1),
            "queue_pressure": self.queue_pressure,
            "consecutive_failures": self.consecutive_failures,
            "distinct_failing_workflows": self.distinct_failing_workflows,
            "failing_workflows": self.failing_workflows,
            "job_failures": [failure.to_dict() for failure in self.job_failures],
            "recent_runs": self.recent_runs,
        }


def collect_telemetry(
    client: GitHubClient,
    owner: str,
    repo: str,
    limit: int = 30,
    inspect_jobs: int = 4,
) -> PipelineTelemetry:
    """Read recent workflow runs and derive pipeline health. Never raises."""
    telemetry = PipelineTelemetry()

    try:
        payload = client.list_workflow_runs(owner, repo, per_page=limit)
    except GitHubError as exc:
        telemetry.error = str(exc)[:300]
        return telemetry

    runs = payload.get("workflow_runs") or []
    telemetry.available = True
    telemetry.has_workflows = bool(runs)
    telemetry.total_runs = len(runs)
    if not runs:
        return telemetry

    queue_delays: list[float] = []
    failing_workflows: set[str] = set()
    now = datetime.now(UTC)

    for run in runs:
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")

        if conclusion == "failure":
            telemetry.failed_runs += 1
            failing_workflows.add(str(run.get("name") or "unnamed"))
        elif conclusion == "success":
            telemetry.successful_runs += 1
        elif conclusion == "cancelled":
            telemetry.cancelled_runs += 1

        if status == "in_progress":
            telemetry.in_progress_runs += 1
        elif status == "queued":
            telemetry.queued_runs += 1
            created = _parse_time(run.get("created_at"))
            if created and (now - created).total_seconds() > QUEUE_CRITICAL_SECONDS:
                telemetry.stuck_queued_runs += 1

        created = _parse_time(run.get("created_at"))
        started = _parse_time(run.get("run_started_at"))
        if created and started:
            delay = (started - created).total_seconds()
            if delay >= 0:
                queue_delays.append(delay)

        telemetry.recent_runs.append(
            {
                "id": run.get("id"),
                "name": run.get("name"),
                "status": status,
                "conclusion": conclusion or None,
                "branch": run.get("head_branch"),
                "created_at": run.get("created_at"),
                "html_url": run.get("html_url"),
                "event": run.get("event"),
            }
        )

    concluded = (
        telemetry.failed_runs + telemetry.successful_runs + telemetry.cancelled_runs
    )
    telemetry.failure_rate = (
        telemetry.failed_runs / concluded if concluded else 0.0
    )

    if queue_delays:
        telemetry.queue_seconds_p50 = statistics.median(queue_delays)
        ordered = sorted(queue_delays)
        index = max(0, int(len(ordered) * 0.95) - 1)
        telemetry.queue_seconds_p95 = ordered[index]
        telemetry.max_queue_seconds = ordered[-1]

    # Consecutive failures, newest first, tell us whether this is a persistent
    # break or a one-off.
    for run in runs:
        if str(run.get("conclusion") or "") == "failure":
            telemetry.consecutive_failures += 1
        elif run.get("conclusion"):
            break

    telemetry.failing_workflows = sorted(failing_workflows)
    telemetry.distinct_failing_workflows = len(failing_workflows)

    telemetry.job_failures = _collect_job_failures(
        client, owner, repo, runs, inspect_jobs
    )
    return telemetry


def _collect_job_failures(
    client: GitHubClient,
    owner: str,
    repo: str,
    runs: list[dict[str, Any]],
    inspect_jobs: int,
) -> list[JobFailure]:
    """Drill into the most recent failed runs to find the failing step."""
    failures: list[JobFailure] = []
    inspected = 0

    for run in runs:
        if inspected >= inspect_jobs:
            break
        if str(run.get("conclusion") or "") != "failure":
            continue
        inspected += 1
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        try:
            jobs_payload = client.list_run_jobs(owner, repo, run_id)
        except GitHubError:
            continue

        for job in jobs_payload.get("jobs") or []:
            if str(job.get("conclusion") or "") not in {"failure", "timed_out"}:
                continue
            failing_step = None
            for step in job.get("steps") or []:
                if str(step.get("conclusion") or "") in {"failure", "timed_out"}:
                    failing_step = str(step.get("name") or "")
                    break
            failures.append(
                JobFailure(
                    run_id=run_id,
                    job_name=str(job.get("name") or "unnamed"),
                    workflow_name=str(run.get("name") or "unnamed"),
                    step_name=failing_step,
                    conclusion=str(job.get("conclusion") or ""),
                    started_at=job.get("started_at"),
                    runner_labels=[str(x) for x in (job.get("labels") or [])],
                )
            )
    return failures


def fetch_job_log_excerpt(
    client: GitHubClient, owner: str, repo: str, job_id: int, max_chars: int = 4000
) -> str:
    """Fetch the tail of a job's log, where the actual error usually sits."""
    try:
        raw = client.request(
            "GET",
            f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
            raw=True,
            expected=(200,),
        )
    except GitHubError:
        return ""
    if not isinstance(raw, bytes):
        return ""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""
    return text[-max_chars:]
