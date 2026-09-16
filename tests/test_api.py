"""HTTP surface: the required health and analyze endpoints."""

import pytest
from fastapi.testclient import TestClient
from healing_agent.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["service"] == "autonomous-cicd-healing-agent"
    assert "checks" in body and "config" in body
    # The health payload must never expose credentials.
    assert "anthropic_api_key" not in str(body).lower()
    assert "token" not in str(body).lower()


def test_analyze_rejects_non_github_url(client):
    response = client.post("/api/analyze", json={
        "repo_url": "https://gitlab.com/a/b",
        "author_name": "A", "branch_name": "fix/x",
    })
    assert response.status_code == 422


def test_analyze_rejects_invalid_branch_name(client):
    response = client.post("/api/analyze", json={
        "repo_url": "https://github.com/o/r",
        "author_name": "A", "branch_name": "bad branch name",
    })
    assert response.status_code == 422


def test_analyze_requires_author_name(client):
    response = client.post("/api/analyze", json={
        "repo_url": "https://github.com/o/r",
        "author_name": "  ", "branch_name": "fix/x",
    })
    assert response.status_code == 422


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/doesnotexist").status_code == 404
    assert client.get("/api/jobs/doesnotexist/report").status_code == 404


def test_api_index_lists_endpoints(client):
    body = client.get("/api").json()
    assert "/api/health" in body["endpoints"]["health"]
    assert "/api/analyze" in body["endpoints"]["analyze"]
