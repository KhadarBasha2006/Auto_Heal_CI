"""Credential handling.

The user hands the agent a GitHub token. It must never appear in a log line,
an event, a stored job record, or an error message.
"""

from healing_agent.jobstore import JobStore
from healing_agent.models import JobStatus, Phase
from healing_agent.redaction import MASK, clear_secrets, register_secret, scrub

TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"


def setup_function():
    clear_secrets()


def test_registered_token_is_scrubbed():
    register_secret(TOKEN)
    assert TOKEN not in scrub(f"cloning with {TOKEN}")
    assert MASK in scrub(f"cloning with {TOKEN}")


def test_token_shape_is_scrubbed_even_if_never_registered():
    """A token the agent was never told about must still not leak."""
    assert "ghp_" not in scrub(f"remote said {TOKEN}")
    assert "github_pat_" not in scrub("github_pat_11ABCDEFG0aaaaaaaaaaaaaaaaaaaaaa")


def test_credentials_in_a_clone_url_are_scrubbed():
    dirty = f"https://x-access-token:{TOKEN}@github.com/owner/repo.git"
    assert TOKEN not in scrub(dirty)


def test_anthropic_key_is_scrubbed():
    assert "sk-ant-" not in scrub("key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA")


def test_nested_structures_are_scrubbed():
    register_secret(TOKEN)
    cleaned = scrub({"a": [f"tok {TOKEN}"], "b": {"c": TOKEN}, "n": 7})
    assert TOKEN not in str(cleaned)
    assert cleaned["n"] == 7


def test_job_snapshot_never_contains_the_token():
    register_secret(TOKEN)
    store = JobStore()
    job = store.create(
        repo_url="https://github.com/o/r", owner="o", repo="r",
        author_name="A", branch_name="b",
    )
    job.log(f"authenticating with {TOKEN}")
    job.set_phase(Phase.CLONING)
    job.finish(JobStatus.FAILED, f"push rejected for {TOKEN}")

    assert TOKEN not in str(job.snapshot())


def test_job_record_does_not_store_the_token_at_all():
    store = JobStore()
    job = store.create(
        repo_url="https://github.com/o/r", owner="o", repo="r",
        author_name="A", branch_name="b",
    )
    assert not hasattr(job, "github_token")
    assert "token" not in job.snapshot()
