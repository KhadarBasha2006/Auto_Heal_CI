import React, { useState } from "react";

export default function ResultPanel({ job }) {
  const [showReport, setShowReport] = useState(false);
  if (!job) return null;

  const finished = ["succeeded", "partial", "failed"].includes(job.status);
  if (!finished && !job.branch_url) return null;

  return (
    <>
      {(job.branch_url || job.branch_preview_url) && (
        <div className="card">
          <div className="branch-cta">
            <div>
              <strong>{job.branch_url ? "Branch pushed:" : "Push attempted:"}</strong>{" "}
              <span className="mono">{job.branch_name}</span>
              {job.commit_sha && (
                <span className="mono" style={{ color: "var(--text-muted)" }}>
                  {" "}· {job.commit_sha.slice(0, 8)}
                </span>
              )}
            </div>
            <div className="branch-links">
              <a className="primary" href={job.branch_url || job.branch_preview_url} target="_blank" rel="noreferrer">
                {job.branch_url ? "View branch" : "Open intended branch URL"}
              </a>
              {job.branch_url && job.compare_url && (
                <a href={job.compare_url} target="_blank" rel="noreferrer">
                  Open pull request
                </a>
              )}
            </div>
          </div>
        </div>
      )}

      {job.error && <div className="banner error">{job.error}</div>}

      {job.incident_report && (
        <div className="card">
          <header>
            <h2>Post-incident report</h2>
            <button className="icon-button" onClick={() => setShowReport((value) => !value)}>
              {showReport ? "Hide" : "Show"}
            </button>
          </header>
          {showReport && <pre className="report">{job.incident_report}</pre>}
        </div>
      )}
    </>
  );
}
