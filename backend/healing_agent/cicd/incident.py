"""Post-incident reporting.

The point of the report is to replace a raw error log with something a human
can act on in thirty seconds: what broke, whose fault it was, what the agent
did about it, and what is still outstanding.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..models import (
    Diagnosis,
    FailureClass,
    Fix,
    Problem,
    RemediationStep,
    Severity,
    ValidationRun,
)
from .statuspage import PlatformStatus
from .telemetry import PipelineTelemetry

VERDICT_HEADLINE = {
    FailureClass.CODE: "Code-level failure - originated in this repository",
    FailureClass.PLATFORM: "Platform-level failure - originated with the provider",
    FailureClass.MIXED: "Mixed failure - code defects during platform degradation",
    FailureClass.UNKNOWN: "Inconclusive - insufficient evidence to attribute",
}


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def build_incident_report(
    *,
    owner: str,
    repo: str,
    branch: str | None,
    diagnosis: Diagnosis | None,
    telemetry: PipelineTelemetry,
    platform_status: PlatformStatus,
    problems: list[Problem],
    fixes: list[Fix],
    validations: list[ValidationRun],
    remediations: list[RemediationStep],
    elapsed_seconds: float,
    score: dict[str, Any],
    branch_url: str | None = None,
) -> str:
    """Render the auto-generated post-incident report as Markdown."""
    lines: list[str] = []
    add = lines.append

    add(f"# Post-Incident Report - {owner}/{repo}")
    add("")
    add(f"**Generated:** {_timestamp()}  ")
    add(f"**Duration:** {elapsed_seconds:.1f}s  ")
    if branch:
        add(f"**Healing branch:** `{branch}`" + (f" - {branch_url}" if branch_url else ""))
    add("")

    # ---------------------------------------------------------------- verdict
    add("## 1. Root cause")
    add("")
    if diagnosis:
        add(f"**Verdict:** {VERDICT_HEADLINE[diagnosis.failure_class]}  ")
        add(f"**Confidence:** {diagnosis.confidence:.0%}  ")
        add(f"**Action taken:** `{diagnosis.recommended_action.value}`")
        add("")
        add(diagnosis.summary)
        add("")
        if diagnosis.evidence:
            add("### Evidence considered")
            add("")
            for item in diagnosis.evidence:
                add(f"- {item}")
            add("")
    else:
        add("No diagnosis was produced for this run.")
        add("")

    # ------------------------------------------------------------- platform
    add("## 2. Platform state")
    add("")
    if platform_status.available:
        add(f"- Provider status: **{platform_status.description}** "
            f"(indicator: `{platform_status.indicator}`)")
        if platform_status.degraded_components:
            add("- Degraded components:")
            for component in platform_status.degraded_components:
                add(f"  - `{component['name']}` - {component['status'].replace('_', ' ')}")
        else:
            add("- No degraded components reported.")
        if platform_status.active_incidents:
            add("- Active incidents:")
            for incident in platform_status.active_incidents:
                add(f"  - **{incident['name']}** (impact: {incident['impact']}, "
                    f"status: {incident['status']})")
    else:
        add(f"- Status page could not be reached: `{platform_status.error}`")
        add("- Platform state is therefore **unverified** for this report.")
    add("")

    # ------------------------------------------------------------ telemetry
    add("## 3. Pipeline telemetry")
    add("")
    if not telemetry.available:
        add(f"- Actions telemetry unavailable: `{telemetry.error or 'no token supplied'}`")
    elif not telemetry.has_workflows:
        add("- No workflow runs found for this repository.")
    else:
        add("| Metric | Value |")
        add("| --- | --- |")
        add(f"| Runs inspected | {telemetry.total_runs} |")
        add(f"| Failed / succeeded | {telemetry.failed_runs} / {telemetry.successful_runs} |")
        add(f"| Failure rate | {telemetry.failure_rate:.0%} |")
        add(f"| Consecutive failures | {telemetry.consecutive_failures} |")
        add(f"| Queue p50 / p95 | {telemetry.queue_seconds_p50:.0f}s / "
            f"{telemetry.queue_seconds_p95:.0f}s |")
        add(f"| Queue pressure | {telemetry.queue_pressure} |")
        add(f"| Stuck queued runs | {telemetry.stuck_queued_runs} |")
        add(f"| Distinct failing workflows | {telemetry.distinct_failing_workflows} |")
        if telemetry.job_failures:
            add("")
            add("Failing jobs:")
            for failure in telemetry.job_failures[:6]:
                step = f" at step '{failure.step_name}'" if failure.step_name else ""
                add(f"- `{failure.workflow_name}` / `{failure.job_name}`{step} "
                    f"({failure.conclusion})")
    add("")

    # -------------------------------------------------------------- defects
    add("## 4. Defects found")
    add("")
    if not problems:
        add("No defects were detected in the repository.")
    else:
        by_severity: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for problem in problems:
            by_severity[problem.severity.value] = by_severity.get(problem.severity.value, 0) + 1
            by_kind[problem.kind.value] = by_kind.get(problem.kind.value, 0) + 1
        add(f"**{len(problems)} total** - "
            + ", ".join(f"{count} {name}" for name, count in sorted(by_severity.items())))
        add("")
        add("By category: " + ", ".join(f"`{k}` x{v}" for k, v in sorted(by_kind.items())))
        add("")
        critical = [p for p in problems if p.severity is Severity.CRITICAL]
        if critical:
            add("### Critical defects")
            add("")
            for problem in critical[:15]:
                add(f"- `{problem.file}:{problem.line}` **{problem.code}** - {problem.message}")
            add("")

    # ---------------------------------------------------------------- fixes
    add("## 5. Repairs applied")
    add("")
    if not fixes:
        add("No repairs were applied.")
    else:
        deterministic = [f for f in fixes if f.tier.value == "deterministic"]
        ai_fixes = [f for f in fixes if f.tier.value == "ai"]
        add(f"**{len(fixes)} repair(s)** - {len(deterministic)} rule-based, "
            f"{len(ai_fixes)} model-generated (each verified before acceptance).")
        add("")
        for fix in fixes:
            add(f"- **`{fix.file}`** ({fix.tier.value}, round {fix.round_index}): "
                f"{fix.description}")
    add("")

    # ----------------------------------------------------------- validation
    add("## 6. Validation")
    add("")
    if not validations:
        add("Validation did not run.")
    else:
        for run in validations:
            verdict = "PASSED" if run.passed else "FAILED"
            add(f"### Round {run.round_index}: {verdict}")
            add("")
            add("| Stage | Result | Duration | Detail |")
            add("| --- | --- | --- | --- |")
            for stage in run.stages:
                mark = "skipped" if stage.skipped else ("pass" if stage.passed else "**fail**")
                detail = stage.detail.replace("|", "\\|")[:110]
                add(f"| `{stage.name}` | {mark} | {stage.duration_ms}ms | {detail} |")
            add("")

    # ---------------------------------------------------------- remediation
    add("## 7. Corrective actions")
    add("")
    if not remediations:
        add("No corrective actions were required.")
    else:
        for step in remediations:
            if step.executed:
                state = "executed, succeeded" if step.succeeded else "executed, failed"
            else:
                state = "planned (not executed)"
            add(f"- **`{step.action.value}`** [{state}] - {step.description}")
            if step.detail:
                add(f"  - {step.detail}")
    add("")

    # ---------------------------------------------------------------- score
    if score:
        add("## 8. Run score")
        add("")
        add(f"**{score.get('total', 0)} / 100** (grade {score.get('grade', 'n/a')})")
        add("")
        for component in score.get("breakdown", []):
            add(f"- {component['label']}: {component['points']}/{component['max']} "
                f"- {component['detail']}")
        add("")

    add("---")
    add("")
    add("*Generated automatically by the Autonomous CI/CD Healing Agent. "
        "Review the diff before merging.*")

    return "\n".join(lines)
