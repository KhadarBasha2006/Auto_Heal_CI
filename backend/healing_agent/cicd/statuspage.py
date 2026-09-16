"""Platform status signals from a Statuspage-compatible API.

GitHub publishes githubstatus.com in the standard Atlassian Statuspage v2
format. Reading it is what lets the agent say "Actions is degraded" instead of
blaming the user's commit for an outage they did not cause.

Every failure mode here is soft: if the status page is unreachable, the agent
records that it could not be consulted and continues on the remaining signals.
Being unable to check the weather is not the same as it being sunny.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

# Statuspage component states, worst-first.
DEGRADED_STATES = {
    "major_outage": 4,
    "partial_outage": 3,
    "degraded_performance": 2,
    "under_maintenance": 1,
    "operational": 0,
}

# Statuspage incident impact levels.
IMPACT_RANK = {"critical": 4, "major": 3, "minor": 2, "maintenance": 1, "none": 0}

# Components whose degradation can plausibly break a pipeline.
PIPELINE_COMPONENTS = {
    "actions", "api requests", "git operations", "packages", "webhooks",
    "codespaces", "pages", "issues", "pull requests",
}


@dataclass
class PlatformStatus:
    """A point-in-time read of the provider's public status page."""

    available: bool = False
    indicator: str = "unknown"
    description: str = "Status page not consulted"
    degraded_components: list[dict[str, Any]] = field(default_factory=list)
    active_incidents: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    source_url: str = ""

    @property
    def pipeline_affected(self) -> bool:
        """True when a component the pipeline depends on is not operational."""
        return any(
            component["name"].lower() in PIPELINE_COMPONENTS
            for component in self.degraded_components
        )

    @property
    def severity_rank(self) -> int:
        component_rank = max(
            (DEGRADED_STATES.get(c["status"], 0) for c in self.degraded_components),
            default=0,
        )
        incident_rank = max(
            (IMPACT_RANK.get(i.get("impact", "none"), 0) for i in self.active_incidents),
            default=0,
        )
        return max(component_rank, incident_rank)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "indicator": self.indicator,
            "description": self.description,
            "pipeline_affected": self.pipeline_affected,
            "severity_rank": self.severity_rank,
            "degraded_components": self.degraded_components,
            "active_incidents": self.active_incidents,
            "error": self.error,
            "source_url": self.source_url,
        }


def fetch_platform_status(
    url: str, timeout: float = 10.0, client: httpx.Client | None = None
) -> PlatformStatus:
    """Read the status page. Never raises."""
    status = PlatformStatus(source_url=url)
    owned = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        response = http.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        status.error = f"{type(exc).__name__}: {str(exc)[:200]}"
        status.description = "Status page unreachable; platform state unknown"
        return status
    finally:
        if owned:
            http.close()

    return parse_status_payload(payload, url)


def parse_status_payload(payload: dict[str, Any], url: str = "") -> PlatformStatus:
    """Translate a Statuspage summary document into a `PlatformStatus`."""
    status = PlatformStatus(available=True, source_url=url)

    overall = payload.get("status") or {}
    status.indicator = str(overall.get("indicator", "none"))
    status.description = str(overall.get("description", "All Systems Operational"))

    for component in payload.get("components") or []:
        if not isinstance(component, dict):
            continue
        state = str(component.get("status", "operational"))
        if state == "operational":
            continue
        # Group headers duplicate their children; skip them.
        if component.get("group"):
            continue
        status.degraded_components.append(
            {
                "name": str(component.get("name", "unknown")),
                "status": state,
                "description": component.get("description") or "",
                "updated_at": component.get("updated_at"),
            }
        )

    for incident in payload.get("incidents") or []:
        if not isinstance(incident, dict):
            continue
        if str(incident.get("status")) in {"resolved", "postmortem"}:
            continue
        updates = incident.get("incident_updates") or []
        latest = updates[0].get("body") if updates and isinstance(updates[0], dict) else ""
        status.active_incidents.append(
            {
                "name": str(incident.get("name", "Unnamed incident")),
                "status": str(incident.get("status", "investigating")),
                "impact": str(incident.get("impact", "none")),
                "created_at": incident.get("created_at"),
                "shortlink": incident.get("shortlink"),
                "latest_update": str(latest)[:400],
            }
        )

    return status
