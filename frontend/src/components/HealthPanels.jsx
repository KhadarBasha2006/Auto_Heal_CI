import React from "react";

export function PipelineHealth({ job }) {
  const health = job?.pipeline_health ?? {};
  const platform = health.platform_status;
  const telemetry = health.telemetry;

  return (
    <div className="card">
      <header>
        <h2>Pipeline &amp; platform health</h2>
      </header>

      <h3 style={{ marginBottom: 8 }}>Provider status</h3>
      {!platform ? (
        <p className="empty">Not checked yet.</p>
      ) : !platform.available ? (
        <div className="banner info">
          Status page unreachable — platform state unverified.
          {platform.error ? ` (${platform.error})` : ""}
        </div>
      ) : (
        <>
          <span className={`pill ${platform.pipeline_affected ? "bad" : "good"}`}>
            <span className="dot" />
            {platform.description}
          </span>
          {platform.degraded_components?.length > 0 && (
            <ul className="evidence" style={{ marginTop: 9 }}>
              {platform.degraded_components.map((component) => (
                <li key={component.name}>
                  <strong>{component.name}</strong> — {component.status.replace(/_/g, " ")}
                </li>
              ))}
            </ul>
          )}
          {platform.active_incidents?.length > 0 && (
            <ul className="evidence" style={{ marginTop: 9 }}>
              {platform.active_incidents.map((incident) => (
                <li key={incident.name} className="platform">
                  <strong>{incident.name}</strong> — impact {incident.impact}, {incident.status}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <h3 style={{ margin: "16px 0 8px" }}>Actions telemetry</h3>
      {!telemetry || !telemetry.available ? (
        <div className="banner info">
          {telemetry?.error
            ? `Unavailable: ${telemetry.error}`
            : "Not available — supply a token with Actions read access."}
        </div>
      ) : !telemetry.has_workflows ? (
        <p className="empty">No workflow runs found for this repository.</p>
      ) : (
        <div className="stats">
          <div className="stat">
            <div className="label">Failure rate</div>
            <div className="value">{Math.round(telemetry.failure_rate * 100)}%</div>
            <div className="sub">{telemetry.failed_runs}/{telemetry.total_runs} runs</div>
          </div>
          <div className="stat">
            <div className="label">Queue p95</div>
            <div className="value">{Math.round(telemetry.queue_seconds_p95)}s</div>
            <div className="sub">pressure: {telemetry.queue_pressure}</div>
          </div>
          <div className="stat">
            <div className="label">Stuck queued</div>
            <div className="value">{telemetry.stuck_queued_runs}</div>
            <div className="sub">awaiting a runner</div>
          </div>
          <div className="stat">
            <div className="label">Failing workflows</div>
            <div className="value">{telemetry.distinct_failing_workflows}</div>
            <div className="sub">
              {telemetry.failing_workflows?.slice(0, 2).join(", ") || "none"}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function RepoHealth({ job }) {
  const health = job?.repo_health;
  if (!health || !health.checks) return null;

  return (
    <div className="card">
      <header>
        <h2>Repository health</h2>
        <span className="pill muted">
          <span className="dot" />
          {health.score}/100 · grade {health.grade}
        </span>
      </header>
      <div className="gates">
        {health.checks.map((check) => (
          <div className={`gate ${check.passed ? "pass" : "fail"}`} key={check.name}>
            <div className="gate-head">
              <span className="gate-name">{check.name.replace(/_/g, " ")}</span>
              <span className="gate-mark">{check.passed ? "ok" : "gap"}</span>
            </div>
            <div className="gate-detail">{check.detail}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
