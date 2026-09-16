"""Autonomous CI/CD healing: detect, diagnose, heal, communicate."""

from __future__ import annotations

from .diagnosis import DiagnosisInput, build_log_corpus, diagnose
from .incident import build_incident_report
from .remediation import (
    RemediationPlan,
    apply_runner_failover,
    backoff_delays,
    remediate,
    retry_with_backoff,
)
from .repo_health import RepoHealth, assess_repo_health
from .statuspage import PlatformStatus, fetch_platform_status, parse_status_payload
from .telemetry import (
    PipelineTelemetry,
    collect_telemetry,
    fetch_job_log_excerpt,
)

__all__ = [
    "DiagnosisInput",
    "PipelineTelemetry",
    "PlatformStatus",
    "RemediationPlan",
    "RepoHealth",
    "apply_runner_failover",
    "assess_repo_health",
    "backoff_delays",
    "build_incident_report",
    "build_log_corpus",
    "collect_telemetry",
    "diagnose",
    "fetch_job_log_excerpt",
    "fetch_platform_status",
    "parse_status_payload",
    "remediate",
    "retry_with_backoff",
]
