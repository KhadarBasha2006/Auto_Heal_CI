"""Failure classification: the user's code, or the platform?

This is the judgement the problem statement turns on. A pipeline that cannot
tell a broken commit from a degraded provider either retries blindly through a
real bug or opens an incident against a commit that was never at fault.

The engine is deliberately rule-first and evidence-carrying. Every signal that
moves the verdict is recorded with its weight, so the conclusion can be audited
rather than taken on faith. A model pass can refine the verdict when logs are
available, but it can never manufacture a verdict the evidence does not support.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Diagnosis, FailureClass, HealingAction
from .statuspage import PlatformStatus
from .telemetry import PipelineTelemetry


@dataclass(frozen=True)
class Signature:
    category: str
    pattern: re.Pattern[str]
    weight: float
    explanation: str


def _compile(category: str, pattern: str, weight: float, explanation: str) -> Signature:
    return Signature(category, re.compile(pattern, re.IGNORECASE), weight, explanation)


# Error text that indicates the platform failed, not the code. These are drawn
# from the failure modes GitHub Actions actually exhibits during incidents --
# including the notorious misleading ones, such as a spurious "account
# suspended" during an auth-service degradation.
PLATFORM_SIGNATURES: tuple[Signature, ...] = (
    _compile("auth", r"bad credentials", 3.0,
             "GitHub rejected valid credentials - typical of an auth-service incident"),
    _compile("auth", r"account (was |has been )?suspended", 3.5,
             "'Account suspended' is a known misleading error during auth outages"),
    _compile("auth", r"could not read (Username|Password)", 2.5,
             "Credential handshake failed before any user code ran"),
    _compile("auth", r"401 Unauthorized", 2.0,
             "Unauthorized response from the platform API"),
    _compile("auth", r"refusing to allow (a|an) (OAuth App|GitHub App|Personal Access Token)", 2.0,
             "Token was refused by the platform authorisation layer"),
    _compile("rate_limit", r"API rate limit exceeded", 3.5,
             "Platform rate limit reached - not a code defect"),
    _compile("rate_limit", r"secondary rate limit", 3.5,
             "Secondary rate limit triggered by the platform"),
    _compile("rate_limit", r"\b429\b|too many requests", 2.5,
             "HTTP 429 from the platform"),
    _compile("capacity", r"no runner matching the specified labels", 3.5,
             "No runner could be assigned - runner pool capacity or label mismatch"),
    _compile("capacity", r"waiting for a runner to pick up this job", 2.5,
             "Job stalled waiting for runner assignment"),
    _compile("capacity", r"runner has received a shutdown signal", 3.0,
             "Runner was reclaimed mid-job by the platform"),
    _compile("capacity", r"lost communication with the server", 3.5,
             "Runner lost contact with the control plane"),
    _compile("capacity", r"the hosted runner.*(lost|failed|did not respond)", 3.0,
             "Hosted runner failure reported by the platform"),
    _compile("network", r"unable to access 'https://github\.com", 3.0,
             "Could not reach github.com from the runner"),
    _compile("network", r"connection reset by peer|connection timed out", 2.0,
             "Network-level failure during the run"),
    _compile("network", r"failed to download action", 3.0,
             "Action could not be fetched from the marketplace"),
    _compile("network", r"TLS handshake timeout|EAI_AGAIN|ETIMEDOUT", 2.0,
             "DNS or TLS failure reaching an external service"),
    _compile("service", r"\b50[0234]\b (Server Error|Bad Gateway|Service Unavailable)?", 2.0,
             "5xx response from a platform service"),
    _compile("service", r"internal server error", 2.0,
             "Platform returned an internal error"),
    _compile("service", r"the operation was canceled", 1.0,
             "Job cancelled without a user-initiated cancel - often infrastructure"),
    _compile("service", r"scheduled maintenance|under maintenance", 2.0,
             "Provider maintenance window in effect"),
)

# Error text that points squarely at the repository's own code or config.
CODE_SIGNATURES: tuple[Signature, ...] = (
    _compile("test", r"AssertionError|assert .* ==|FAILED .*::", 3.0,
             "Test assertions failed - the code under test misbehaved"),
    _compile("test", r"\d+ failed,? \d* ?passed|Tests:.*failed", 3.0,
             "Test suite reported failures"),
    _compile("syntax", r"SyntaxError|IndentationError|TabError", 3.5,
             "Source file does not parse"),
    _compile("syntax", r"Unexpected token|Parsing error", 3.0,
             "Parser rejected the source"),
    _compile("types", r"error TS\d+|Type error:|mypy: error", 3.0,
             "Static type checking failed"),
    _compile("imports", r"ModuleNotFoundError|ImportError|Cannot find module", 2.5,
             "An import could not be resolved - usually a missing declaration"),
    _compile("lint", r"ruff|eslint|flake8|pylint.*\b(error|E\d{3})", 2.0,
             "Linter reported errors"),
    _compile("build", r"compilation (failed|error)|build failed", 2.5,
             "Build step failed"),
    _compile("code", r"NameError|TypeError|AttributeError|ValueError|KeyError", 2.5,
             "Unhandled runtime exception from application code"),
    _compile("config", r"invalid workflow file|workflow is not valid", 3.0,
             "The workflow definition itself is malformed"),
)


@dataclass
class DiagnosisInput:
    """Everything the classifier is allowed to reason from."""

    telemetry: PipelineTelemetry
    platform_status: PlatformStatus
    log_text: str = ""
    local_critical_problems: int = 0
    local_validation_failed: bool = False
    recent_workflow_change: bool = False


def diagnose(data: DiagnosisInput) -> Diagnosis:
    """Weigh platform evidence against code evidence and return a verdict."""
    platform_score = 0.0
    code_score = 0.0
    evidence: list[str] = []
    categories: set[str] = set()

    # ---- Signal 1: the provider's own status page --------------------------
    status = data.platform_status
    if status.available:
        if status.pipeline_affected:
            weight = 2.0 + status.severity_rank
            platform_score += weight
            categories.add("service")
            names = ", ".join(
                f"{c['name']} ({c['status'].replace('_', ' ')})"
                for c in status.degraded_components[:4]
            )
            evidence.append(
                f"[platform +{weight:.1f}] Provider status page reports degraded "
                f"components affecting pipelines: {names}."
            )
        if status.active_incidents:
            weight = 1.5 + max(
                (
                    {"critical": 2.5, "major": 2.0, "minor": 1.0}.get(i["impact"], 0.5)
                    for i in status.active_incidents
                ),
                default=0.5,
            )
            platform_score += weight
            evidence.append(
                f"[platform +{weight:.1f}] Active provider incident: "
                f"'{status.active_incidents[0]['name']}' "
                f"(impact: {status.active_incidents[0]['impact']})."
            )
        if not status.pipeline_affected and not status.active_incidents:
            evidence.append(
                "[neutral] Provider status page reports all pipeline "
                "components operational."
            )
    else:
        evidence.append(
            "[neutral] Provider status page could not be reached; "
            "platform state is unverified."
        )

    # ---- Signal 2: queue latency and stuck jobs ----------------------------
    telemetry = data.telemetry
    if telemetry.available:
        if telemetry.stuck_queued_runs:
            platform_score += 3.0
            categories.add("capacity")
            evidence.append(
                f"[platform +3.0] {telemetry.stuck_queued_runs} run(s) stuck in "
                f"the queue past the 10-minute threshold - runners are not "
                f"being assigned."
            )
        if telemetry.queue_pressure == "critical":
            platform_score += 2.5
            categories.add("capacity")
            evidence.append(
                f"[platform +2.5] Queue latency is critical "
                f"(max {telemetry.max_queue_seconds:.0f}s, "
                f"p95 {telemetry.queue_seconds_p95:.0f}s)."
            )
        elif telemetry.queue_pressure == "elevated":
            platform_score += 1.0
            categories.add("capacity")
            evidence.append(
                f"[platform +1.0] Queue latency elevated "
                f"(p95 {telemetry.queue_seconds_p95:.0f}s)."
            )

        # Unrelated workflows breaking together points away from any one commit.
        if telemetry.distinct_failing_workflows >= 3:
            platform_score += 2.0
            evidence.append(
                f"[platform +2.0] {telemetry.distinct_failing_workflows} distinct "
                f"workflows are failing simultaneously "
                f"({', '.join(telemetry.failing_workflows[:4])}) - broad, "
                f"cross-cutting failure."
            )
        elif telemetry.distinct_failing_workflows == 1 and telemetry.failed_runs:
            code_score += 1.5
            evidence.append(
                f"[code +1.5] Failures are confined to a single workflow "
                f"('{telemetry.failing_workflows[0]}') - localised, which "
                f"points at the change rather than the platform."
            )

        if telemetry.failure_rate >= 0.8 and telemetry.total_runs >= 5:
            evidence.append(
                f"[context] Failure rate across the last {telemetry.total_runs} "
                f"runs is {telemetry.failure_rate:.0%}."
            )

    # ---- Signal 3: error text from the failing jobs ------------------------
    if data.log_text:
        for signature in PLATFORM_SIGNATURES:
            if signature.pattern.search(data.log_text):
                platform_score += signature.weight
                categories.add(signature.category)
                evidence.append(
                    f"[platform +{signature.weight:.1f}] {signature.explanation}."
                )
        for signature in CODE_SIGNATURES:
            if signature.pattern.search(data.log_text):
                code_score += signature.weight
                evidence.append(
                    f"[code +{signature.weight:.1f}] {signature.explanation}."
                )

    # ---- Signal 4: what we found in the repository ourselves ---------------
    # This is the strongest code-side evidence available: the defect was
    # reproduced locally, with no dependency on the platform at all.
    if data.local_critical_problems:
        weight = min(4.0, 1.5 + data.local_critical_problems * 0.5)
        code_score += weight
        evidence.append(
            f"[code +{weight:.1f}] Static analysis reproduced "
            f"{data.local_critical_problems} critical defect(s) in the "
            f"repository, independently of any CI run."
        )
    if data.local_validation_failed:
        code_score += 2.5
        evidence.append(
            "[code +2.5] The local validation pipeline failed on this checkout, "
            "so the failure reproduces without the provider."
        )
    if data.recent_workflow_change:
        code_score += 1.5
        categories.add("config")
        evidence.append(
            "[code +1.5] A workflow/CI configuration file changed recently - a "
            "config regression is a likely cause."
        )

    return _verdict(platform_score, code_score, evidence, categories, data)


def _verdict(
    platform_score: float,
    code_score: float,
    evidence: list[str],
    categories: set[str],
    data: DiagnosisInput,
) -> Diagnosis:
    """Convert accumulated scores into a class, confidence, and action."""
    total = platform_score + code_score
    signals = {
        "platform_score": round(platform_score, 2),
        "code_score": round(code_score, 2),
        "categories": sorted(categories),
        "queue_pressure": data.telemetry.queue_pressure
        if data.telemetry.available
        else "unknown",
        "status_page_available": data.platform_status.available,
        "local_critical_problems": data.local_critical_problems,
    }

    if total < 1.0:
        return Diagnosis(
            failure_class=FailureClass.UNKNOWN,
            confidence=0.25,
            summary=(
                "No decisive evidence either way. The pipeline shows no active "
                "failure signature, and the provider reports no relevant "
                "degradation."
            ),
            evidence=evidence or ["No failure signals observed."],
            recommended_action=HealingAction.NO_ACTION,
            signals=signals,
        )

    margin = abs(platform_score - code_score)
    dominant = max(platform_score, code_score)
    # Confidence rises with both the margin and the absolute weight of evidence.
    confidence = min(0.97, 0.45 + (margin / max(total, 1.0)) * 0.4 + min(dominant, 8) * 0.02)

    if margin < max(1.5, total * 0.2):
        return Diagnosis(
            failure_class=FailureClass.MIXED,
            confidence=round(min(confidence, 0.7), 3),
            summary=(
                f"Both platform and code signals are present "
                f"(platform {platform_score:.1f} vs code {code_score:.1f}). "
                f"Treat the code defects as real, but expect platform noise to "
                f"affect verification."
            ),
            evidence=evidence,
            recommended_action=HealingAction.FIX_CODE,
            signals=signals,
        )

    if platform_score > code_score:
        # Locally reproduced defects are ground truth about the code and are
        # not in competition with the platform evidence -- both can be true at
        # once. A verdict of pure PLATFORM would tell the operator to failover
        # and leave a real syntax error unfixed, so the floor here is MIXED.
        if data.local_validation_failed or data.local_critical_problems:
            return Diagnosis(
                failure_class=FailureClass.MIXED,
                confidence=round(min(confidence, 0.8), 3),
                summary=(
                    f"The platform is degraded (evidence {platform_score:.1f}), "
                    f"but this repository also has defects that reproduce "
                    f"locally with no provider involved (code evidence "
                    f"{code_score:.1f}). Both are real: the code is repaired "
                    f"here, and the platform fault is handled separately."
                ),
                evidence=evidence
                + [
                    "[rule] Verdict held at MIXED rather than PLATFORM: local "
                    "reproduction proves the code defects independently of the "
                    "platform outage."
                ],
                recommended_action=HealingAction.FIX_CODE,
                signals=signals,
            )
        action, summary = _platform_action(categories, data, platform_score)
        return Diagnosis(
            failure_class=FailureClass.PLATFORM,
            confidence=round(confidence, 3),
            summary=summary,
            evidence=evidence,
            recommended_action=action,
            signals=signals,
        )

    action = (
        HealingAction.ROLLBACK_CONFIG
        if "config" in categories and data.recent_workflow_change
        else HealingAction.FIX_CODE
    )
    return Diagnosis(
        failure_class=FailureClass.CODE,
        confidence=round(confidence, 3),
        summary=(
            f"The failure originates in this repository, not the platform "
            f"(code evidence {code_score:.1f} vs platform {platform_score:.1f}). "
            f"Repairing the source is the correct response; retrying would just "
            f"reproduce the same failure."
        ),
        evidence=evidence,
        recommended_action=action,
        signals=signals,
    )


def _platform_action(
    categories: set[str], data: DiagnosisInput, score: float
) -> tuple[HealingAction, str]:
    """Choose the corrective action that fits the kind of platform failure."""
    if "capacity" in categories:
        return (
            HealingAction.FAILOVER_RUNNER,
            f"Runner capacity is the bottleneck (platform evidence {score:.1f}). "
            f"Jobs are not being assigned runners, so waiting on the same pool "
            f"will not help; failing over to an alternate runner is the "
            f"appropriate response.",
        )
    if "rate_limit" in categories:
        return (
            HealingAction.RETRY_WITH_BACKOFF,
            f"A provider rate limit was hit (platform evidence {score:.1f}). "
            f"The work is valid and will succeed once the limit window resets, "
            f"so a backed-off retry is correct - and retrying immediately would "
            f"deepen the limit.",
        )
    if "auth" in categories:
        return (
            HealingAction.RETRY_WITH_BACKOFF,
            f"Authentication failed at the platform layer (evidence {score:.1f}). "
            f"During auth incidents GitHub returns misleading errors such as "
            f"'account suspended' for accounts that are perfectly healthy, so "
            f"this is retried with backoff and escalated if it persists.",
        )
    if "network" in categories:
        return (
            HealingAction.RETRY_WITH_BACKOFF,
            f"Network reachability failed during the run (evidence {score:.1f}). "
            f"Transient connectivity faults clear on their own, so a backed-off "
            f"retry is the right first response.",
        )
    if data.platform_status.severity_rank >= 3:
        return (
            HealingAction.REROUTE_PIPELINE,
            f"The provider is in a declared major incident (evidence {score:.1f}). "
            f"Retrying into a degraded platform wastes minutes; work should be "
            f"rerouted to an alternate provider or held until the incident "
            f"clears.",
        )
    return (
        HealingAction.RETRY_WITH_BACKOFF,
        f"The evidence points to a platform-side fault (evidence {score:.1f}) "
        f"rather than a defect in this repository. A backed-off retry is the "
        f"correct first response.",
    )


def build_log_corpus(telemetry: PipelineTelemetry, extra: str = "") -> str:
    """Assemble the searchable text the signature matcher runs against."""
    parts: list[str] = []
    for failure in telemetry.job_failures:
        parts.append(f"{failure.workflow_name} / {failure.job_name}")
        if failure.step_name:
            parts.append(f"step: {failure.step_name}")
        if failure.log_excerpt:
            parts.append(failure.log_excerpt)
    if extra:
        parts.append(extra)
    return "\n".join(parts)
