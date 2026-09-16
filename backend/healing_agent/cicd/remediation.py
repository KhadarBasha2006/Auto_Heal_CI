"""Corrective actions.

Actions are split by blast radius, which decides what runs autonomously:

* Changes written into the agent's own branch (runner failover, config
  rollback) are produced automatically. They land on a new branch the user
  reviews before merging, so the worst case is a diff they discard.
* Anything that touches the live pipeline (re-running jobs on the real
  repository) is planned but only executed when explicitly authorised, because
  the agent cannot un-run a job it should not have started.

That boundary is why `execute` is off by default and threaded through
explicitly rather than inferred.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..github import GitHubClient, GitHubError
from ..models import Diagnosis, FailureClass, HealingAction, RemediationStep

# Repository variable teams can flip to move work onto another runner pool.
RUNNER_VARIABLE = "HEALING_AGENT_RUNNER"

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 2.0
DEFAULT_MAX_DELAY = 60.0


def backoff_delays(
    attempts: int = DEFAULT_MAX_ATTEMPTS,
    base: float = DEFAULT_BASE_DELAY,
    cap: float = DEFAULT_MAX_DELAY,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> list[float]:
    """Exponential backoff with full jitter.

    Full jitter (a uniform draw from [0, computed]) is used rather than a fixed
    schedule so that many agents recovering from the same outage do not
    synchronise into a thundering herd against a provider that is already
    struggling.
    """
    generator = rng or random
    delays: list[float] = []
    for attempt in range(attempts):
        computed = min(cap, base * (2**attempt))
        delays.append(generator.uniform(0, computed) if jitter else computed)
    return delays


def retry_with_backoff(
    operation: Callable[[], Any],
    attempts: int = DEFAULT_MAX_ATTEMPTS,
    base: float = DEFAULT_BASE_DELAY,
    cap: float = DEFAULT_MAX_DELAY,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> tuple[bool, Any, list[str]]:
    """Run `operation`, retrying transient failures with backed-off delays."""
    log: list[str] = []
    delays = backoff_delays(attempts, base, cap, rng=rng)

    for attempt in range(1, attempts + 1):
        try:
            result = operation()
        except Exception as exc:
            log.append(f"attempt {attempt}: failed - {type(exc).__name__}: {exc}")
            if attempt == attempts:
                return False, exc, log
            delay = delays[attempt - 1]
            log.append(f"waiting {delay:.1f}s before retry")
            sleep(delay)
            continue
        log.append(f"attempt {attempt}: succeeded")
        return True, result, log

    return False, None, log  # pragma: no cover - loop always returns


# ------------------------------------------------------------ runner failover


_RUNS_ON = re.compile(
    r"^(?P<indent>\s*)runs-on:\s*(?P<value>.+?)\s*$", re.MULTILINE
)


def apply_runner_failover(
    workflow_text: str, variable: str = RUNNER_VARIABLE
) -> tuple[str, int]:
    """Make every `runs-on:` overridable by a repository variable.

    The original label stays as the default, so behaviour is unchanged until
    someone sets the variable. That turns a runner-capacity outage into a
    one-setting failover instead of an emergency pull request against every
    workflow in the repo.
    """
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        value = match.group("value").strip()
        # Already parameterised, or a matrix/expression reference - leave it.
        if variable in value or value.startswith("${{"):
            return match.group(0)
        # Only rewrite simple scalar labels; lists and mappings are left alone.
        if value.startswith(("[", "{")):
            return match.group(0)
        quoted = value.strip("'\"")
        changed += 1
        return (
            f"{match.group('indent')}runs-on: "
            f"${{{{ vars.{variable} || '{quoted}' }}}}"
        )

    return _RUNS_ON.sub(replace, workflow_text), changed


def plan_runner_failover(
    root: Path, execute: bool = True
) -> tuple[RemediationStep, list[str]]:
    """Rewrite workflow files so their runner can be switched centrally."""
    workflows = sorted((root / ".github" / "workflows").glob("*.y*ml")) if (
        root / ".github" / "workflows"
    ).is_dir() else []

    if not workflows:
        return (
            RemediationStep(
                action=HealingAction.FAILOVER_RUNNER,
                description="No GitHub Actions workflows found to parameterise.",
                executed=False,
                succeeded=None,
                detail="Repository has no .github/workflows directory.",
            ),
            [],
        )

    touched: list[str] = []
    total = 0
    for path in workflows:
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rewritten, count = apply_runner_failover(original)
        if count and rewritten != original:
            total += count
            touched.append(str(path.relative_to(root)).replace("\\", "/"))
            if execute:
                path.write_text(rewritten, encoding="utf-8")

    if not touched:
        return (
            RemediationStep(
                action=HealingAction.FAILOVER_RUNNER,
                description="Workflows already support runner failover.",
                executed=False,
                succeeded=None,
                detail="Every `runs-on:` is already parameterised or non-scalar.",
            ),
            [],
        )

    return (
        RemediationStep(
            action=HealingAction.FAILOVER_RUNNER,
            description=(
                f"Parameterised {total} `runs-on:` declaration(s) across "
                f"{len(touched)} workflow file(s) so the runner pool can be "
                f"switched by setting the {RUNNER_VARIABLE} repository "
                f"variable - no workflow edit needed during an outage."
            ),
            executed=execute,
            succeeded=True if execute else None,
            detail=f"Files: {', '.join(touched)}",
        ),
        touched,
    )


# ----------------------------------------------------------- config rollback


def find_workflow_regression(
    client: GitHubClient, owner: str, repo: str, workflow_path: str
) -> dict[str, Any] | None:
    """Find the commit that last changed a workflow file, and the one before."""
    try:
        commits = client.list_commits(owner, repo, per_page=10, path=workflow_path)
    except GitHubError:
        return None
    if len(commits) < 2:
        return None
    return {
        "path": workflow_path,
        "current_sha": commits[0].get("sha"),
        "current_message": (commits[0].get("commit") or {}).get("message", "")[:200],
        "current_date": ((commits[0].get("commit") or {}).get("author") or {}).get("date"),
        "previous_sha": commits[1].get("sha"),
        "previous_message": (commits[1].get("commit") or {}).get("message", "")[:200],
    }


def plan_config_rollback(
    client: GitHubClient | None,
    owner: str,
    repo: str,
    root: Path,
    changed_workflows: list[str],
) -> RemediationStep:
    """Identify the workflow change that most likely broke the pipeline."""
    if not changed_workflows:
        return RemediationStep(
            action=HealingAction.ROLLBACK_CONFIG,
            description="No recent workflow configuration change to roll back.",
            executed=False,
            succeeded=None,
        )
    if client is None:
        return RemediationStep(
            action=HealingAction.ROLLBACK_CONFIG,
            description=(
                f"Workflow change detected in {changed_workflows[0]}, but no "
                f"GitHub token was supplied to read its history."
            ),
            executed=False,
            succeeded=None,
        )

    regression = find_workflow_regression(client, owner, repo, changed_workflows[0])
    if not regression:
        return RemediationStep(
            action=HealingAction.ROLLBACK_CONFIG,
            description=(
                f"Could not establish a rollback target for "
                f"{changed_workflows[0]} (insufficient history)."
            ),
            executed=False,
            succeeded=None,
        )

    return RemediationStep(
        action=HealingAction.ROLLBACK_CONFIG,
        description=(
            f"Identified rollback candidate for {regression['path']}: revert "
            f"{str(regression['current_sha'])[:8]} "
            f"(\"{regression['current_message'].splitlines()[0][:80]}\") back to "
            f"{str(regression['previous_sha'])[:8]}."
        ),
        executed=False,
        succeeded=None,
        detail=(
            "Rollback is reported rather than performed: reverting a commit on "
            "the user's default branch is outside what this agent does without "
            "explicit authorisation."
        ),
    )


# ------------------------------------------------------------- pipeline retry


def trigger_rerun(
    client: GitHubClient,
    owner: str,
    repo: str,
    run_id: int,
    execute: bool = False,
    attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> RemediationStep:
    """Re-run the failed jobs of a workflow run, with backoff."""
    if not execute:
        delays = backoff_delays(attempts, jitter=False)
        return RemediationStep(
            action=HealingAction.RETRY_WITH_BACKOFF,
            description=(
                f"Planned: re-run failed jobs of run {run_id} with exponential "
                f"backoff ({', '.join(f'{d:.0f}s' for d in delays)})."
            ),
            executed=False,
            succeeded=None,
            detail=(
                "Not executed - re-running jobs on the live repository requires "
                "explicit authorisation (set execute_remediation=true)."
            ),
            attempt=0,
        )

    succeeded, result, log = retry_with_backoff(
        lambda: client.rerun_failed_jobs(owner, repo, run_id),
        attempts=attempts,
        sleep=sleep,
    )
    return RemediationStep(
        action=HealingAction.RETRY_WITH_BACKOFF,
        description=f"Re-ran failed jobs of workflow run {run_id}.",
        executed=True,
        succeeded=succeeded,
        detail=" | ".join(log)[:400],
        attempt=len([entry for entry in log if entry.startswith("attempt")]),
        backoff_seconds=sum(backoff_delays(attempts, jitter=False)),
    )


# ------------------------------------------------------------------ dispatch


@dataclass
class RemediationPlan:
    steps: list[RemediationStep]
    workflow_files_changed: list[str]


def remediate(
    diagnosis: Diagnosis,
    root: Path,
    client: GitHubClient | None,
    owner: str,
    repo: str,
    changed_workflows: list[str],
    failing_run_id: int | None = None,
    execute_pipeline_actions: bool = False,
) -> RemediationPlan:
    """Choose and carry out the corrective action the diagnosis calls for."""
    steps: list[RemediationStep] = []
    workflow_files: list[str] = []
    action = diagnosis.recommended_action

    if action is HealingAction.FAILOVER_RUNNER:
        step, touched = plan_runner_failover(root, execute=True)
        steps.append(step)
        workflow_files.extend(touched)
        # Capacity outages usually recover; queue a retry behind the failover.
        if client and failing_run_id:
            steps.append(
                trigger_rerun(
                    client, owner, repo, failing_run_id,
                    execute=execute_pipeline_actions,
                )
            )

    elif action is HealingAction.RETRY_WITH_BACKOFF:
        if client and failing_run_id:
            steps.append(
                trigger_rerun(
                    client, owner, repo, failing_run_id,
                    execute=execute_pipeline_actions,
                )
            )
        else:
            delays = backoff_delays(jitter=False)
            steps.append(
                RemediationStep(
                    action=HealingAction.RETRY_WITH_BACKOFF,
                    description=(
                        f"Recommended: retry the pipeline with exponential "
                        f"backoff ({', '.join(f'{d:.0f}s' for d in delays)}). "
                        f"No specific failed run was identified to re-trigger."
                    ),
                    executed=False,
                    succeeded=None,
                )
            )

    elif action is HealingAction.ROLLBACK_CONFIG:
        steps.append(
            plan_config_rollback(client, owner, repo, root, changed_workflows)
        )

    elif action is HealingAction.REROUTE_PIPELINE:
        step, touched = plan_runner_failover(root, execute=True)
        steps.append(step)
        workflow_files.extend(touched)
        steps.append(
            RemediationStep(
                action=HealingAction.REROUTE_PIPELINE,
                description=(
                    "Provider is in a declared major incident. Workflows have "
                    "been parameterised for runner failover; route builds to an "
                    "alternate provider or self-hosted pool until it clears."
                ),
                executed=False,
                succeeded=None,
                detail=(
                    f"Set the {RUNNER_VARIABLE} repository variable to your "
                    f"alternate runner label to activate the reroute."
                ),
            )
        )

    elif action is HealingAction.FIX_CODE:
        steps.append(
            RemediationStep(
                action=HealingAction.FIX_CODE,
                description=(
                    "Defects were located in this repository; the agent's "
                    "repair pipeline handles them directly rather than "
                    "retrying a build that would fail identically."
                ),
                executed=True,
                succeeded=True,
            )
        )

    elif action is HealingAction.HOLD_AND_ALERT:
        steps.append(
            RemediationStep(
                action=HealingAction.HOLD_AND_ALERT,
                description=(
                    "Evidence is inconclusive. Holding automated action and "
                    "escalating to a human is safer than guessing."
                ),
                executed=False,
                succeeded=None,
            )
        )

    else:
        steps.append(
            RemediationStep(
                action=HealingAction.NO_ACTION,
                description=(
                    "Pipeline is healthy; no corrective action required."
                    if diagnosis.failure_class is FailureClass.UNKNOWN
                    else "No corrective action mapped for this diagnosis."
                ),
                executed=False,
                succeeded=None,
            )
        )

    return RemediationPlan(steps=steps, workflow_files_changed=workflow_files)
