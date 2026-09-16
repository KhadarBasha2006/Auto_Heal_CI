"""FastAPI application.

Required surface:
  GET  /api/health   - health check
  POST /api/analyze  - the one main endpoint that runs the agent

Supporting endpoints exist so the dashboard can stream progress and read the
final report, but everything the agent does is driven by /api/analyze.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from . import __version__
from .analysis import ruff_available
from .cicd import fetch_platform_status
from .config import get_settings
from .jobstore import event_stream, get_store
from .models import (
    AnalyzeAccepted,
    AnalyzeRequest,
    HealthResponse,
    JobStatus,
)
from .pipeline import run_pipeline
from .redaction import register_secret, scrub

STARTED_AT = time.time()

app = FastAPI(
    title="Autonomous CI/CD Healing Agent",
    description=(
        "Clones a repository, detects real defects, repairs them, validates "
        "the result through a CI/CD-style gate loop, and pushes a healed "
        "branch -- while classifying pipeline failures as code-level or "
        "platform-level and responding to each appropriately."
    ),
    version=__version__,
)

settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Ensure no error response can leak a token in a stack trace or message."""
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": scrub(str(exc))[:500]},
    )


# --------------------------------------------------------------------- health


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    """Liveness and capability check.

    Reports which optional capabilities are actually present so a deployment
    problem (missing git binary, missing API key) is visible here rather than
    surfacing as a confusing failure mid-run.
    """
    store = get_store()
    checks: dict[str, Any] = {
        "job_store": "ok",
        "active_jobs": store.active_count(),
        "total_jobs": store.count(),
        "git_binary": bool(shutil.which("git")),
        "node_binary": bool(shutil.which("node")),
        "ruff": ruff_available(),
        "ai_repair_tier": settings.ai_enabled,
        "workspace_writable": settings.workspace_root.exists(),
        "test_execution_allowed": settings.allow_test_execution,
    }
    degraded = not checks["workspace_writable"] or not checks["ruff"]
    return HealthResponse(
        status="degraded" if degraded else "ok",
        version=__version__,
        uptime_seconds=round(time.time() - STARTED_AT, 2),
        checks=checks,
        config=settings.describe(),
    )


@app.get("/api/health/platform", tags=["health"])
async def platform_health() -> dict[str, Any]:
    """Live provider status, used by the dashboard's platform banner."""
    status = await asyncio.to_thread(
        fetch_platform_status, settings.status_page_url
    )
    return status.to_dict()


# -------------------------------------------------------------------- analyze


@app.post(
    "/api/analyze",
    response_model=AnalyzeAccepted,
    status_code=202,
    tags=["agent"],
)
async def analyze(payload: AnalyzeRequest) -> AnalyzeAccepted:
    """Start an autonomous healing run.

    Returns immediately with a job id; progress streams from the returned
    stream_url. The token is used only for the lifetime of the run and is
    never written to the job record or any log line.
    """
    try:
        owner, repo = payload.owner_repo()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not payload.github_token and not settings.fallback_github_token:
        # Not fatal: the agent can still analyse a public repo and report.
        # Pushing is what needs the token, so this is a warning, not an error.
        pass

    register_secret(payload.github_token)

    store = get_store()
    job = store.create(
        repo_url=payload.repo_url,
        owner=owner,
        repo=repo,
        author_name=payload.author_name,
        branch_name=payload.branch_name,
        base_branch=payload.base_branch,
    )
    job.bind_loop(asyncio.get_running_loop())
    job.log(
        f"Job accepted for {owner}/{repo} -> branch '{payload.branch_name}' "
        f"(author: {payload.author_name})"
    )

    async def runner() -> None:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(run_pipeline, job, payload, settings),
                timeout=settings.job_timeout_seconds,
            )
        except TimeoutError:
            job.log(
                f"Run exceeded the {settings.job_timeout_seconds}s limit "
                f"and was stopped.",
                "error",
            )
            if not job.terminal:
                job.finish(JobStatus.FAILED, "Run timed out.")
        except Exception as exc:  # noqa: BLE001
            if not job.terminal:
                job.finish(JobStatus.FAILED, scrub(str(exc)))

    asyncio.create_task(runner())

    return AnalyzeAccepted(
        job_id=job.id,
        status=job.status,
        poll_url=f"/api/jobs/{job.id}",
        stream_url=f"/api/jobs/{job.id}/events",
        report_url=f"/api/jobs/{job.id}/report",
    )


# ----------------------------------------------------------------------- jobs


@app.get("/api/jobs", tags=["jobs"])
async def list_jobs(limit: int = 20) -> dict[str, Any]:
    store = get_store()
    return {
        "jobs": [
            {
                "job_id": job.id,
                "status": job.status.value,
                "phase": job.phase.value,
                "progress": job.progress,
                "repo": f"{job.owner}/{job.repo}",
                "branch_name": job.branch_name,
                "problems_found": len(job.problems),
                "fixes_applied": len(job.fixes),
                "elapsed_seconds": round(job.elapsed_seconds, 2),
                "score": job.score.get("total"),
                "created_at": job.created_at,
            }
            for job in store.list(limit=min(limit, 50))
        ]
    }


@app.get("/api/jobs/{job_id}", tags=["jobs"])
async def get_job(job_id: str) -> dict[str, Any]:
    """Full job snapshot. Polling this is equivalent to consuming the stream."""
    job = get_store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.snapshot()


@app.get("/api/jobs/{job_id}/events", tags=["jobs"])
async def stream_job(job_id: str) -> StreamingResponse:
    """Server-Sent Events feed powering the live dashboard."""
    job = get_store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def generate():
        try:
            async for event in event_stream(job):
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:  # client disconnected
            raise

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/jobs/{job_id}/report", tags=["jobs"])
async def job_report(job_id: str) -> PlainTextResponse:
    """The auto-generated post-incident report, as Markdown."""
    job = get_store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.incident_report:
        raise HTTPException(
            status_code=409,
            detail=f"Report not ready; job is currently '{job.status.value}'.",
        )
    return PlainTextResponse(job.incident_report, media_type="text/markdown")


def _mount_dashboard() -> None:
    """Serve the built React dashboard from this process, when it exists.

    A single origin serving both the UI and the API is the simplest thing to
    deploy and share: one URL, no CORS to configure, no build-time backend
    address baked into the bundle. When `frontend/dist` is absent (local
    development, where Vite serves the UI on its own port) this is a no-op.

    Mounted last so it can never shadow an /api route.
    """
    from fastapi.staticfiles import StaticFiles

    package_root = Path(__file__).resolve().parent
    repo_root = package_root.parent.parent
    for candidate in (
        repo_root / "frontend" / "dist",     # repo checkout and Docker image
        package_root / "static",             # vendored copy, if ever used
        Path("/app/frontend/dist"),          # container WORKDIR
        Path.cwd() / "frontend" / "dist",    # host launched from repo root
    ):
        if candidate.is_dir() and (candidate / "index.html").is_file():
            app.mount(
                "/", StaticFiles(directory=str(candidate), html=True), name="dashboard"
            )
            return


@app.get("/api", tags=["meta"])
async def api_root() -> dict[str, Any]:
    return {
        "service": "autonomous-cicd-healing-agent",
        "version": __version__,
        "endpoints": {
            "health": "GET /api/health",
            "analyze": "POST /api/analyze",
            "platform_health": "GET /api/health/platform",
            "job": "GET /api/jobs/{job_id}",
            "stream": "GET /api/jobs/{job_id}/events",
            "report": "GET /api/jobs/{job_id}/report",
            "jobs": "GET /api/jobs",
        },
    }


# Registered after all API routes so the catch-all mount cannot shadow them.
_mount_dashboard()
