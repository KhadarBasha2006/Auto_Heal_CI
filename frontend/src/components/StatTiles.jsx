import React from "react";

const STATUS_LABEL = {
  queued: "Queued", running: "Running", succeeded: "Succeeded",
  partial: "Partial", failed: "Failed", cancelled: "Cancelled",
};

export default function StatTiles({ job, elapsed }) {
  const problems = job?.problems_found ?? 0;
  const fixes = job?.fixes_applied ?? 0;
  const resolved = job?.score?.metrics?.problems_resolved ?? 0;
  const status = job?.status ?? "queued";
  const score = job?.score?.total;
  const grade = job?.score?.grade;

  const finished = ["succeeded", "partial", "failed", "cancelled"].includes(status);
  const rate = job?.score?.metrics?.fixes_per_minute;

  const tiles = [
    { label: "Bugs found", value: problems,
      sub: job?.files_scanned ? `across ${job.files_scanned} files` : "scanning…" },
    { label: "Fixes applied", value: fixes,
      sub: resolved ? `${resolved} problems resolved` : "no repairs yet" },
    { label: "Time taken", value: `${elapsed.toFixed(1)}s`,
      sub: finished
        ? (rate ? `${rate} fixes/min` : "complete")
        : "in progress" },
    { label: "Status", value: STATUS_LABEL[status] ?? status, text: true,
      sub: finished
        ? (job?.validation_passed ? "validation passed" : "validation did not pass")
        : "validation pending" },
    { label: "Score", value: score != null ? score : "—",
      sub: grade ? `grade ${grade}` : "computed at completion" },
  ];

  return (
    <div className="card">
      <header><h2>Run metrics</h2></header>
      <div className="stats">
        {tiles.map((tile) => (
          <div className="stat" key={tile.label}>
            <div className="label">{tile.label}</div>
            <div className={`value${tile.text ? " value-text" : ""}`}>
              {tile.value}
            </div>
            <div className="sub">{tile.sub}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
