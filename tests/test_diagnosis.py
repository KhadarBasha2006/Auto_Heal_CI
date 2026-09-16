"""Failure classification: code-level vs platform-level."""

from healing_agent.cicd.diagnosis import DiagnosisInput, diagnose
from healing_agent.cicd.statuspage import parse_status_payload
from healing_agent.cicd.telemetry import PipelineTelemetry
from healing_agent.models import FailureClass, HealingAction

HEALTHY = parse_status_payload(
    {
        "status": {"indicator": "none", "description": "All Systems Operational"},
        "components": [{"name": "Actions", "status": "operational"}],
        "incidents": [],
    }
)

OUTAGE = parse_status_payload(
    {
        "status": {"indicator": "major", "description": "Partial System Outage"},
        "components": [{"name": "Actions", "status": "major_outage"}],
        "incidents": [
            {
                "name": "Elevated queue times",
                "status": "investigating",
                "impact": "major",
                "incident_updates": [{"body": "investigating"}],
            }
        ],
    }
)


def _telemetry(**kwargs) -> PipelineTelemetry:
    base = dict(available=True, has_workflows=True, total_runs=10)
    base.update(kwargs)
    return PipelineTelemetry(**base)


def test_broken_code_on_healthy_platform_is_code():
    result = diagnose(
        DiagnosisInput(
            telemetry=_telemetry(
                failed_runs=2, failure_rate=0.2,
                distinct_failing_workflows=1, failing_workflows=["CI"],
            ),
            platform_status=HEALTHY,
            log_text="FAILED tests/test_a.py\nAssertionError: assert 3 == 4",
            local_critical_problems=3,
            local_validation_failed=True,
        )
    )
    assert result.failure_class is FailureClass.CODE
    assert result.recommended_action is HealingAction.FIX_CODE


def test_runner_capacity_outage_is_platform_failover():
    result = diagnose(
        DiagnosisInput(
            telemetry=_telemetry(
                failed_runs=9, failure_rate=0.9, stuck_queued_runs=3,
                max_queue_seconds=1800, queue_seconds_p95=1500,
                distinct_failing_workflows=4,
                failing_workflows=["CI", "Deploy", "Docs", "Nightly"],
            ),
            platform_status=OUTAGE,
            log_text="No runner matching the specified labels was found",
        )
    )
    assert result.failure_class is FailureClass.PLATFORM
    assert result.recommended_action is HealingAction.FAILOVER_RUNNER


def test_rate_limit_is_platform_retry():
    result = diagnose(
        DiagnosisInput(
            telemetry=_telemetry(failed_runs=5, failure_rate=0.5,
                                 distinct_failing_workflows=3,
                                 failing_workflows=["a", "b", "c"]),
            platform_status=HEALTHY,
            log_text="You have exceeded a secondary rate limit",
        )
    )
    assert result.failure_class is FailureClass.PLATFORM
    assert result.recommended_action is HealingAction.RETRY_WITH_BACKOFF


def test_misleading_account_suspended_is_not_blamed_on_the_user():
    """During auth incidents GitHub reports 'account suspended' for healthy
    accounts. That must classify as platform, never as the user's code."""
    result = diagnose(
        DiagnosisInput(
            telemetry=_telemetry(failed_runs=2, failure_rate=0.2,
                                 distinct_failing_workflows=1,
                                 failing_workflows=["CI"]),
            platform_status=HEALTHY,
            log_text="remote: This account has been suspended. Bad credentials",
        )
    )
    assert result.failure_class is FailureClass.PLATFORM
    assert "auth" in result.signals["categories"]


def test_local_reproduction_outranks_a_platform_outage():
    """A defect reproduced locally is platform-independent ground truth, so the
    verdict must never come back as pure PLATFORM and skip repairing it."""
    result = diagnose(
        DiagnosisInput(
            telemetry=_telemetry(
                failed_runs=9, failure_rate=0.9, stuck_queued_runs=3,
                max_queue_seconds=1800, queue_seconds_p95=1500,
                distinct_failing_workflows=4,
                failing_workflows=["CI", "Deploy", "Docs", "Nightly"],
            ),
            platform_status=OUTAGE,
            log_text="SyntaxError: invalid syntax\nno runner matching the specified labels",
            local_critical_problems=2,
            local_validation_failed=True,
        )
    )
    assert result.failure_class is FailureClass.MIXED
    assert result.recommended_action is HealingAction.FIX_CODE


def test_healthy_pipeline_yields_no_action():
    result = diagnose(
        DiagnosisInput(
            telemetry=_telemetry(successful_runs=10, failure_rate=0.0),
            platform_status=HEALTHY,
        )
    )
    assert result.failure_class is FailureClass.UNKNOWN
    assert result.recommended_action is HealingAction.NO_ACTION


def test_every_verdict_carries_evidence():
    result = diagnose(
        DiagnosisInput(
            telemetry=_telemetry(failed_runs=1),
            platform_status=HEALTHY,
            log_text="AssertionError",
            local_critical_problems=1,
        )
    )
    assert result.evidence, "a verdict without evidence is not auditable"
    assert 0.0 <= result.confidence <= 1.0
