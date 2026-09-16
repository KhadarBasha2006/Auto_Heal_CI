"""Run scoring.

The score answers "how well did this run go", combining how much was actually
repaired with how fast it happened and whether the result survived validation.

Repair credit is severity-weighted on purpose. Ten unused-import cleanups are
not worth one fixed syntax error, and a score that treated them as equal would
reward the agent for chasing trivia.
"""

from __future__ import annotations

from typing import Any

from .models import Problem, Severity, ValidationRun

# Seconds under which a run is considered fast. Beyond SLOW_BUDGET it scores 0
# for speed, with a linear ramp between the two.
FAST_BUDGET = 45.0
SLOW_BUDGET = 420.0

WEIGHTS = {"repair": 40, "validation": 25, "speed": 20, "criticals": 15}


def _grade(total: int) -> str:
    for threshold, letter in (
        (93, "A+"), (85, "A"), (77, "B+"), (70, "B"),
        (62, "C+"), (55, "C"), (45, "D"),
    ):
        if total >= threshold:
            return letter
    return "F"


def _severity_weight(problems: list[Problem]) -> int:
    return sum(problem.severity.weight for problem in problems)


def compute_score(
    problems_before: list[Problem],
    problems_after: list[Problem],
    fixes_applied: int,
    elapsed_seconds: float,
    validations: list[ValidationRun],
) -> dict[str, Any]:
    """Grade a completed run out of 100, with an explainable breakdown."""
    breakdown: list[dict[str, Any]] = []

    # ---- Repair effectiveness (severity-weighted) --------------------------
    before_weight = _severity_weight(problems_before)
    after_weight = _severity_weight(problems_after)
    if before_weight == 0:
        repair_ratio = 1.0
        repair_detail = "Repository was already clean; full credit."
    else:
        repair_ratio = max(0.0, (before_weight - after_weight) / before_weight)
        repair_detail = (
            f"Resolved {before_weight - after_weight} of {before_weight} "
            f"severity-weighted defect points "
            f"({len(problems_before) - len(problems_after)} of "
            f"{len(problems_before)} findings)."
        )
    repair_points = round(WEIGHTS["repair"] * repair_ratio)
    breakdown.append(
        {
            "key": "repair",
            "label": "Repair effectiveness",
            "points": repair_points,
            "max": WEIGHTS["repair"],
            "detail": repair_detail,
        }
    )

    # ---- Validation --------------------------------------------------------
    if not validations:
        validation_points = 0
        validation_detail = "Validation did not run."
    else:
        final = validations[-1]
        # An intentionally disabled gate is neutral, but a server-blocked
        # requested gate is skipped with passed=False and must count as a
        # failed gate. This prevents every run from receiving the same score.
        gates = [stage for stage in final.stages if not stage.skipped or not stage.passed]
        passed = sum(1 for stage in gates if stage.passed)
        ratio = passed / len(gates) if gates else 0.0
        validation_points = round(WEIGHTS["validation"] * ratio)
        validation_detail = (
            f"{passed}/{len(gates)} active gate(s) passed on the final round"
            + (f" (round {final.round_index})." if final.round_index else ".")
        )
    breakdown.append(
        {
            "key": "validation",
            "label": "Validation gates",
            "points": validation_points,
            "max": WEIGHTS["validation"],
            "detail": validation_detail,
        }
    )

    # ---- Speed -------------------------------------------------------------
    if elapsed_seconds <= FAST_BUDGET:
        speed_ratio = 1.0
    elif elapsed_seconds >= SLOW_BUDGET:
        speed_ratio = 0.0
    else:
        speed_ratio = 1.0 - (elapsed_seconds - FAST_BUDGET) / (SLOW_BUDGET - FAST_BUDGET)
    speed_points = round(WEIGHTS["speed"] * speed_ratio)
    breakdown.append(
        {
            "key": "speed",
            "label": "Speed",
            "points": speed_points,
            "max": WEIGHTS["speed"],
            "detail": (
                f"Completed in {elapsed_seconds:.1f}s "
                f"(full credit under {FAST_BUDGET:.0f}s, none beyond "
                f"{SLOW_BUDGET:.0f}s)."
            ),
        }
    )

    # ---- Critical defect elimination --------------------------------------
    criticals_before = sum(
        1 for p in problems_before if p.severity is Severity.CRITICAL
    )
    criticals_after = sum(
        1 for p in problems_after if p.severity is Severity.CRITICAL
    )
    if criticals_before == 0:
        critical_ratio = 1.0
        critical_detail = "No critical defects were present."
    else:
        critical_ratio = max(
            0.0, (criticals_before - criticals_after) / criticals_before
        )
        critical_detail = (
            f"Eliminated {criticals_before - criticals_after} of "
            f"{criticals_before} critical defect(s)."
        )
    critical_points = round(WEIGHTS["criticals"] * critical_ratio)
    breakdown.append(
        {
            "key": "criticals",
            "label": "Critical defects cleared",
            "points": critical_points,
            "max": WEIGHTS["criticals"],
            "detail": critical_detail,
        }
    )

    raw_total = repair_points + validation_points + speed_points + critical_points
    total = raw_total
    final_validation = validations[-1] if validations else None
    executable_gates = (
        [stage for stage in final_validation.stages if not stage.skipped or not stage.passed]
        if final_validation else []
    )
    if not final_validation or not executable_gates:
        total = min(total, 95)
    total = max(0, min(100, total))

    # Keep the displayed breakdown mathematically consistent with the final
    # score. If a score is bounded for missing validation evidence, scale its
    # component points instead of showing full-credit rows beside a lower total.
    if raw_total and total < raw_total:
        scaled = []
        for item in breakdown:
            exact = item["points"] * total / raw_total
            item["points"] = int(exact)
            scaled.append(exact - item["points"])
        remainder = total - sum(item["points"] for item in breakdown)
        for index in sorted(range(len(breakdown)), key=scaled.__getitem__, reverse=True)[:remainder]:
            breakdown[index]["points"] += 1

    return {
        "total": total,
        "grade": _grade(total),
        "breakdown": breakdown,
        "metrics": {
            "problems_before": len(problems_before),
            "problems_after": len(problems_after),
            "problems_resolved": max(0, len(problems_before) - len(problems_after)),
            "fixes_applied": fixes_applied,
            "criticals_before": criticals_before,
            "criticals_after": criticals_after,
            "elapsed_seconds": round(elapsed_seconds, 2),
            "fixes_per_minute": round(
                fixes_applied / (elapsed_seconds / 60), 2
            )
            if elapsed_seconds > 0
            else 0.0,
            "severity_points_before": before_weight,
            "severity_points_after": after_weight,
        },
    }
