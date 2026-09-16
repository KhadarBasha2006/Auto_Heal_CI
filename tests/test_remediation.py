"""Corrective actions."""

import random

import yaml
from healing_agent.cicd.remediation import (
    apply_runner_failover,
    backoff_delays,
    retry_with_backoff,
)


def test_backoff_is_exponential_and_capped():
    delays = backoff_delays(6, base=2.0, cap=60.0, jitter=False)
    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0]


def test_jitter_spreads_retries():
    """Full jitter keeps many agents from retrying in lockstep against a
    provider that is already degraded."""
    generator = random.Random(1)
    delays = backoff_delays(5, rng=generator)
    ceiling = backoff_delays(5, jitter=False)
    assert all(0 <= actual <= limit for actual, limit in zip(delays, ceiling, strict=True))
    assert len(set(delays)) > 1


def test_retry_succeeds_after_transient_failures():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("503 Service Unavailable")
        return "ok"

    succeeded, result, log = retry_with_backoff(
        flaky, attempts=5, sleep=lambda _: None
    )
    assert succeeded and result == "ok" and attempts["n"] == 3
    assert any("succeeded" in line for line in log)


def test_retry_gives_up_and_reports():
    def always_fails():
        raise RuntimeError("permanent")

    succeeded, result, _ = retry_with_backoff(
        always_fails, attempts=3, sleep=lambda _: None
    )
    assert not succeeded and isinstance(result, RuntimeError)


WORKFLOW = """name: CI
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: make
  matrixed:
    runs-on: ${{ matrix.os }}
    steps:
      - run: echo
  listed:
    runs-on: [self-hosted, linux]
    steps:
      - run: echo
"""


def test_runner_failover_parameterises_only_scalar_labels():
    updated, count = apply_runner_failover(WORKFLOW)
    assert count == 1, "matrix expressions and list forms must be left alone"
    assert "vars.HEALING_AGENT_RUNNER || 'ubuntu-latest'" in updated
    assert "${{ matrix.os }}" in updated
    assert "[self-hosted, linux]" in updated


def test_runner_failover_output_is_valid_yaml():
    updated, _ = apply_runner_failover(WORKFLOW)
    yaml.safe_load(updated)


def test_runner_failover_is_idempotent():
    once, first = apply_runner_failover(WORKFLOW)
    twice, second = apply_runner_failover(once)
    assert second == 0 and twice == once
