"""In-memory job registry and the pub/sub event bus behind the live dashboard.

Each analyze run is a `Job`. Progress is published as discrete events that the
dashboard consumes over Server-Sent Events; the full snapshot is also readable
by polling, so a client that cannot hold an SSE connection (some serverless
edges buffer streams) still sees identical state.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .models import (
    Diagnosis,
    Fix,
    JobStatus,
    Phase,
    Problem,
    RemediationStep,
    ValidationRun,
)
from .redaction import scrub

# Ordered phases, used to derive a monotonic progress percentage.
_PHASE_PROGRESS: dict[Phase, int] = {
    Phase.QUEUED: 0,
    Phase.CLONING: 10,
    Phase.SCANNING: 28,
    Phase.DIAGNOSING: 42,
    Phase.HEALING: 60,
    Phase.VALIDATING: 78,
    Phase.PUSHING: 90,
    Phase.REPORTING: 96,
    Phase.DONE: 100,
}

MAX_LOG_ENTRIES = 600
MAX_JOBS_RETAINED = 50


@dataclass
class LogEntry:
    ts: float
    level: str
    message: str
    phase: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "level": self.level,
            "message": self.message,
            "phase": self.phase,
        }


@dataclass
class Job:
    """Mutable state for a single analyze run."""

    id: str
    repo_url: str
    owner: str
    repo: str
    author_name: str
    branch_name: str
    base_branch: str | None = None

    status: JobStatus = JobStatus.QUEUED
    phase: Phase = Phase.QUEUED
    progress: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    problems: list[Problem] = field(default_factory=list)
    fixes: list[Fix] = field(default_factory=list)
    validations: list[ValidationRun] = field(default_factory=list)
    remediations: list[RemediationStep] = field(default_factory=list)
    diagnosis: Diagnosis | None = None

    pipeline_health: dict[str, Any] = field(default_factory=dict)
    repo_health: dict[str, Any] = field(default_factory=dict)
    score: dict[str, Any] = field(default_factory=dict)
    incident_report: str = ""
    branch_url: str | None = None
    branch_preview_url: str | None = None
    commit_sha: str | None = None
    compare_url: str | None = None
    pull_request_url: str | None = None
    error: str | None = None
    files_scanned: int = 0
    languages: dict[str, int] = field(default_factory=dict)

    logs: deque[LogEntry] = field(default_factory=lambda: deque(maxlen=MAX_LOG_ENTRIES))
    _subscribers: list[asyncio.Queue] = field(default_factory=list, repr=False)
    _seq: int = 0
    _loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    _loop_thread_id: int | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ---------------------------------------------------------------- timing
    @property
    def elapsed_seconds(self) -> float:
        start = self.started_at or self.created_at
        end = self.finished_at or time.time()
        return max(0.0, end - start)

    @property
    def terminal(self) -> bool:
        return self.status in {
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }

    # ------------------------------------------------------------ publishing
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the event loop that owns the subscriber queues.

        The pipeline itself runs on a worker thread (it shells out to git and
        blocks on network I/O), so events raised there must be handed back to
        the loop rather than pushed onto an asyncio queue cross-thread.
        """
        self._loop = loop
        self._loop_thread_id = threading.get_ident()

    @staticmethod
    def _deliver(queue: asyncio.Queue, event: dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - slow consumer
            pass

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            seq = self._seq
            subscribers = list(self._subscribers)
        event = {
            "seq": seq,
            "type": event_type,
            "ts": time.time(),
            "job_id": self.id,
            "data": scrub(payload),
        }
        on_loop_thread = (
            self._loop_thread_id is None
            or threading.get_ident() == self._loop_thread_id
        )
        for queue in subscribers:
            if on_loop_thread or self._loop is None:
                self._deliver(queue, event)
            else:
                try:
                    self._loop.call_soon_threadsafe(self._deliver, queue, event)
                except RuntimeError:  # pragma: no cover - loop already closed
                    pass

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    # -------------------------------------------------------------- mutation
    def log(self, message: str, level: str = "info") -> None:
        entry = LogEntry(
            ts=time.time(),
            level=level,
            message=scrub(message),
            phase=self.phase.value,
        )
        self.logs.append(entry)
        self._publish("log", entry.to_dict())

    def set_phase(self, phase: Phase, message: str | None = None) -> None:
        self.phase = phase
        self.progress = max(self.progress, _PHASE_PROGRESS.get(phase, self.progress))
        if self.status == JobStatus.QUEUED and phase != Phase.QUEUED:
            self.status = JobStatus.RUNNING
            self.started_at = self.started_at or time.time()
        self._publish(
            "phase",
            {
                "phase": phase.value,
                "progress": self.progress,
                "status": self.status.value,
                "elapsed_seconds": round(self.elapsed_seconds, 2),
            },
        )
        if message:
            self.log(message)

    def bump_progress(self, value: int) -> None:
        """Nudge progress forward within a phase, never backwards."""
        new_value = max(self.progress, min(99, value))
        if new_value != self.progress:
            self.progress = new_value
            self._publish(
                "progress",
                {
                    "progress": self.progress,
                    "elapsed_seconds": round(self.elapsed_seconds, 2),
                },
            )

    def add_problems(self, problems: list[Problem]) -> None:
        if not problems:
            return
        self.problems.extend(problems)
        self._publish(
            "problems",
            {
                "added": [problem.to_dict() for problem in problems],
                "total": len(self.problems),
                "by_severity": self.problem_counts(),
            },
        )

    def add_fix(self, fix: Fix) -> None:
        self.fixes.append(fix)
        self._publish(
            "fix", {"fix": fix.to_dict(), "total_fixes": len(self.fixes)}
        )

    def add_validation(self, run: ValidationRun) -> None:
        self.validations.append(run)
        self._publish("validation", run.to_dict())

    def add_remediation(self, step: RemediationStep) -> None:
        self.remediations.append(step)
        self._publish("remediation", step.to_dict())

    def set_diagnosis(self, diagnosis: Diagnosis) -> None:
        self.diagnosis = diagnosis
        self._publish("diagnosis", diagnosis.to_dict())

    def set_pipeline_health(self, health: dict[str, Any]) -> None:
        self.pipeline_health = health
        self._publish("pipeline_health", health)

    def set_repo_health(self, health: dict[str, Any]) -> None:
        self.repo_health = health
        self._publish("repo_health", health)

    def set_score(self, score: dict[str, Any]) -> None:
        self.score = score
        self._publish("score", score)

    def finish(self, status: JobStatus, error: str | None = None) -> None:
        self.status = status
        self.finished_at = time.time()
        self.phase = Phase.DONE
        self.progress = 100
        self.error = scrub(error) if error else None
        self._publish("done", self.snapshot())

    # ------------------------------------------------------------- reporting
    def problem_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for problem in self.problems:
            counts[problem.severity.value] = counts.get(problem.severity.value, 0) + 1
        return counts

    def problem_kinds(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for problem in self.problems:
            counts[problem.kind.value] = counts.get(problem.kind.value, 0) + 1
        return counts

    def snapshot(self) -> dict[str, Any]:
        """Full serialisable state. This is what the dashboard renders."""
        latest_validation = self.validations[-1] if self.validations else None
        return scrub(
            {
                "job_id": self.id,
                "status": self.status.value,
                "phase": self.phase.value,
                "progress": self.progress,
                "repo_url": self.repo_url,
                "owner": self.owner,
                "repo": self.repo,
                "author_name": self.author_name,
                "branch_name": self.branch_name,
                "base_branch": self.base_branch,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "elapsed_seconds": round(self.elapsed_seconds, 2),
                "files_scanned": self.files_scanned,
                "languages": self.languages,
                "problems_found": len(self.problems),
                "problems": [problem.to_dict() for problem in self.problems],
                "problems_by_severity": self.problem_counts(),
                "problems_by_kind": self.problem_kinds(),
                "fixes_applied": len(self.fixes),
                "fixes": [fix.to_dict() for fix in self.fixes],
                "validations": [run.to_dict() for run in self.validations],
                "validation_passed": bool(latest_validation and latest_validation.passed),
                "diagnosis": self.diagnosis.to_dict() if self.diagnosis else None,
                "remediations": [step.to_dict() for step in self.remediations],
                "pipeline_health": self.pipeline_health,
                "repo_health": self.repo_health,
                "score": self.score,
                "incident_report": self.incident_report,
                "branch_url": self.branch_url,
                "branch_preview_url": self.branch_preview_url,
                "compare_url": self.compare_url,
                "pull_request_url": self.pull_request_url,
                "commit_sha": self.commit_sha,
                "error": self.error,
                "logs": [entry.to_dict() for entry in self.logs],
            }
        )


class JobStore:
    """Bounded, process-local registry of jobs."""

    def __init__(self, max_jobs: int = MAX_JOBS_RETAINED) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: deque[str] = deque()
        self._max_jobs = max_jobs

    def create(self, **kwargs: Any) -> Job:
        job_id = uuid.uuid4().hex[:16]
        job = Job(id=job_id, **kwargs)
        self._jobs[job_id] = job
        self._order.append(job_id)
        self._evict()
        return job

    def _evict(self) -> None:
        while len(self._order) > self._max_jobs:
            oldest = self._order.popleft()
            job = self._jobs.get(oldest)
            # Never evict a job that is still running.
            if job and not job.terminal:
                self._order.append(oldest)
                return
            self._jobs.pop(oldest, None)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, limit: int = 20) -> list[Job]:
        ids = list(self._order)[-limit:]
        return [self._jobs[i] for i in reversed(ids) if i in self._jobs]

    def count(self) -> int:
        return len(self._jobs)

    def active_count(self) -> int:
        return sum(1 for job in self._jobs.values() if not job.terminal)


_store: JobStore | None = None


def get_store() -> JobStore:
    global _store
    if _store is None:
        _store = JobStore()
    return _store


async def event_stream(job: Job) -> AsyncIterator[dict[str, Any]]:
    """Yield an initial snapshot, then every subsequent event until done.

    A heartbeat is emitted while idle so proxies do not close the connection.
    """
    queue = job.subscribe()
    try:
        yield {"seq": 0, "type": "snapshot", "ts": time.time(), "job_id": job.id,
               "data": job.snapshot()}
        if job.terminal:
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except TimeoutError:
                yield {"type": "heartbeat", "ts": time.time(), "job_id": job.id,
                       "data": {"elapsed_seconds": round(job.elapsed_seconds, 2)}}
                if job.terminal:
                    return
                continue
            yield event
            if event["type"] == "done":
                return
    finally:
        job.unsubscribe(queue)
