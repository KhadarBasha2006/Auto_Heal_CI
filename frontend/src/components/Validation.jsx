import React from "react";

function mark(stage) {
  if (stage.skipped) return "skip";
  return stage.passed ? "pass" : "fail";
}

export default function Validation({ job }) {
  const runs = job?.validations ?? [];
  const latest = runs[runs.length - 1];
  const skippedStages = latest?.stages?.filter((stage) => stage.skipped) ?? [];

  return (
    <div className="card">
      <header>
        <h2>CI/CD validation</h2>
        <span className="hint">
          {runs.length > 0
            ? `round ${latest.round} — ${latest.passed ? "all gates passed" : "gates failed"}`
            : "waiting for first round"}
        </span>
      </header>

      {!latest ? (
        <p className="empty">Validation has not run yet.</p>
      ) : (
        <>
          <div className="gates">
            {latest.stages.map((stage) => (
              <div className={`gate ${mark(stage)}`} key={stage.name}>
                <div className="gate-head">
                  <span className="gate-name">{stage.name}</span>
                  <span className="gate-mark">{mark(stage)}</span>
                </div>
                <div className="gate-detail">{stage.detail}</div>
                <div className="gate-detail">{stage.duration_ms}ms</div>
              </div>
            ))}
          </div>
          {runs.length > 1 && (
            <p className="hint" style={{ marginTop: 10 }}>
              {runs.length} heal/validate rounds ran:{" "}
              {runs.map((run) => `R${run.round} ${run.passed ? "pass" : "fail"}`).join(" → ")}
            </p>
          )}
          {skippedStages.length > 0 && (
            <p className="hint validation-note">
              Skipped gates are not proof of success: {skippedStages.map((stage) => stage.name).join(", ")}.
              {skippedStages.some((stage) => stage.name === "tests")
                ? " Enable HEALING_AGENT_ALLOW_TEST_EXECUTION=true only for trusted repositories to run tests."
                : ""}
            </p>
          )}
        </>
      )}
    </div>
  );
}
