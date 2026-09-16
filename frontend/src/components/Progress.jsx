import React from "react";

const PHASES = [
  "queued", "cloning", "scanning", "diagnosing",
  "healing", "validating", "pushing", "reporting", "done",
];

export default function Progress({ job, elapsed }) {
  const phase = job?.phase ?? "queued";
  const progress = job?.progress ?? 0;
  const status = job?.status ?? "queued";
  const index = PHASES.indexOf(phase);

  const running = status === "queued" || status === "running";
  const fillClass = [
    status === "failed" ? "failed" : status === "succeeded" ? "done" : "",
    running ? "active" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="card">
      <div className="progress-head">
        <span className="progress-phase">
          {phase === "done" ? status : phase}
        </span>
        <span className="progress-meta">
          {progress}% · {elapsed.toFixed(1)}s elapsed
        </span>
      </div>
      <div
        className="progress-track"
        role="progressbar"
        aria-valuenow={progress}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Agent progress"
      >
        <div className={`progress-fill ${fillClass}`} style={{ width: `${progress}%` }} />
      </div>
      <div className="phases">
        {PHASES.slice(0, -1).map((name, position) => (
          <span
            key={name}
            className={`phase-chip ${
              position === index ? "active" : position < index ? "complete" : ""
            }`}
          >
            {name}
          </span>
        ))}
      </div>
    </div>
  );
}
