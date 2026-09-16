"""Pydantic request/response schemas and internal domain records."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def weight(self) -> int:
        return {"critical": 10, "high": 6, "medium": 3, "low": 1}[self.value]


class ProblemKind(str, Enum):
    SYNTAX = "syntax"
    INDENTATION = "indentation"
    IMPORT = "import"
    TYPE = "type"
    LINT = "lint"
    FORMATTING = "formatting"
    CONFIG = "config"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Phase(str, Enum):
    QUEUED = "queued"
    CLONING = "cloning"
    SCANNING = "scanning"
    DIAGNOSING = "diagnosing"
    HEALING = "healing"
    VALIDATING = "validating"
    PUSHING = "pushing"
    REPORTING = "reporting"
    DONE = "done"


class FixTier(str, Enum):
    DETERMINISTIC = "deterministic"
    AI = "ai"


class FailureClass(str, Enum):
    """Root-cause bucket for a pipeline failure."""

    CODE = "code"
    PLATFORM = "platform"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class HealingAction(str, Enum):
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    FAILOVER_RUNNER = "failover_runner"
    REROUTE_PIPELINE = "reroute_pipeline"
    ROLLBACK_CONFIG = "rollback_config"
    FIX_CODE = "fix_code"
    HOLD_AND_ALERT = "hold_and_alert"
    NO_ACTION = "no_action"


# --------------------------------------------------------------------------
# API request / response models
# --------------------------------------------------------------------------

_REPO_URL_RE = re.compile(
    r"^(?:https?://|git@)?"
    r"(?:www\.)?github\.com[:/]"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9._-]+?)"
    r"(?:\.git)?/?$"
)

_BRANCH_INVALID = re.compile(r"[~^:?*\[\]\\\x00-\x20]")


class AnalyzeRequest(BaseModel):
    """Payload for POST /api/analyze."""

    repo_url: str = Field(
        ...,
        description="GitHub repository URL, e.g. https://github.com/owner/repo",
        examples=["https://github.com/octocat/Hello-World"],
    )
    author_name: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Name recorded as the commit author.",
    )
    branch_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Branch the agent creates and pushes fixes to.",
    )
    github_token: str | None = Field(
        default=None,
        description=(
            "GitHub token with 'repo' (or fine-grained Contents: read/write) "
            "scope. Held in memory for the duration of the run, never logged "
            "and never persisted."
        ),
    )
    author_email: str | None = Field(
        default=None,
        description="Commit author email. Defaults to a noreply address.",
    )
    base_branch: str | None = Field(
        default=None,
        description="Branch to start from. Defaults to the repo's default branch.",
    )
    max_rounds: int | None = Field(
        default=None, ge=1, le=10, description="Heal/validate rounds to attempt."
    )
    push: bool = Field(
        default=True,
        description="Push the healed branch. Set false for a dry run.",
    )
    run_tests: bool = Field(
        default=True,
        description="Run the repository's own test suite during validation.",
    )
    use_ai: bool = Field(
        default=True,
        description="Allow the AI repair tier (requires ANTHROPIC_API_KEY).",
    )

    @field_validator("repo_url")
    @classmethod
    def _validate_repo_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("repo_url must not be empty")
        if not _REPO_URL_RE.match(value):
            raise ValueError(
                "repo_url must be a GitHub repository URL such as "
                "https://github.com/owner/repo"
            )
        return value

    @field_validator("branch_name")
    @classmethod
    def _validate_branch(cls, value: str) -> str:
        value = value.strip().strip("/")
        if not value:
            raise ValueError("branch_name must not be empty")
        if _BRANCH_INVALID.search(value):
            raise ValueError(
                "branch_name contains characters git does not allow "
                "(whitespace, ~, ^, :, ?, *, [, ], backslash)"
            )
        if value.startswith("-") or ".." in value or value.endswith(".lock"):
            raise ValueError("branch_name is not a valid git ref")
        return value

    @field_validator("author_name")
    @classmethod
    def _validate_author(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("author_name must not be empty")
        return value

    def owner_repo(self) -> tuple[str, str]:
        match = _REPO_URL_RE.match(self.repo_url.strip())
        if not match:  # pragma: no cover - guarded by the validator
            raise ValueError("repo_url is not a GitHub repository URL")
        return match.group("owner"), match.group("repo")


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    service: str = "autonomous-cicd-healing-agent"
    version: str
    uptime_seconds: float
    checks: dict[str, Any]
    config: dict[str, Any]


class AnalyzeAccepted(BaseModel):
    job_id: str
    status: JobStatus
    poll_url: str
    stream_url: str
    report_url: str


# --------------------------------------------------------------------------
# Internal domain records
# --------------------------------------------------------------------------


@dataclass
class Problem:
    """A single detected defect in the repository."""

    file: str
    line: int
    column: int
    kind: ProblemKind
    severity: Severity
    code: str
    message: str
    detector: str
    snippet: str | None = None
    auto_fixable: bool = False

    @property
    def key(self) -> str:
        return f"{self.file}:{self.line}:{self.code}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "detector": self.detector,
            "snippet": self.snippet,
            "auto_fixable": self.auto_fixable,
        }


@dataclass
class Fix:
    """A repair the agent applied to the working tree."""

    file: str
    tier: FixTier
    description: str
    problems_addressed: list[str] = field(default_factory=list)
    lines_changed: int = 0
    round_index: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "tier": self.tier.value,
            "description": self.description,
            "problems_addressed": self.problems_addressed,
            "lines_changed": self.lines_changed,
            "round": self.round_index,
        }


@dataclass
class StageResult:
    """Outcome of one gate in the CI/CD validation loop."""

    name: str
    passed: bool
    duration_ms: int
    detail: str
    skipped: bool = False
    problem_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "skipped": self.skipped,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
            "problem_count": self.problem_count,
        }


@dataclass
class ValidationRun:
    """One full pass through the validation pipeline."""

    round_index: int
    stages: list[StageResult] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_index,
            "passed": self.passed,
            "stages": [stage.to_dict() for stage in self.stages],
        }


@dataclass
class Diagnosis:
    """Verdict on whether a failure is the user's code or the platform."""

    failure_class: FailureClass
    confidence: float
    summary: str
    evidence: list[str] = field(default_factory=list)
    recommended_action: HealingAction = HealingAction.NO_ACTION
    signals: dict[str, Any] = field(default_factory=dict)
    source: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class.value,
            "confidence": round(self.confidence, 3),
            "summary": self.summary,
            "evidence": self.evidence,
            "recommended_action": self.recommended_action.value,
            "signals": self.signals,
            "source": self.source,
        }


@dataclass
class RemediationStep:
    """A corrective action the healing agent planned or performed."""

    action: HealingAction
    description: str
    executed: bool
    succeeded: bool | None = None
    detail: str = ""
    attempt: int = 1
    backoff_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "description": self.description,
            "executed": self.executed,
            "succeeded": self.succeeded,
            "detail": self.detail,
            "attempt": self.attempt,
            "backoff_seconds": round(self.backoff_seconds, 2),
        }
