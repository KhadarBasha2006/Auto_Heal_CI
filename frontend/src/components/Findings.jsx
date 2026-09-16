import React, { useState } from "react";

const ORDER = ["critical", "high", "medium", "low"];

/**
 * Severity is shown as separately labelled rows, never as adjacent colour-only
 * segments of one stacked bar. Two of the status hues sit below the
 * normal-vision separation floor, so each row carries its name and count in
 * text and colour is only ever reinforcement.
 */
export function SeverityBreakdown({ counts }) {
  const total = ORDER.reduce((sum, key) => sum + (counts?.[key] ?? 0), 0);
  if (!total) return <p className="empty">No problems detected.</p>;

  return (
    <div className="severity-rows">
      {ORDER.map((name) => {
        const count = counts?.[name] ?? 0;
        const pct = total ? (count / total) * 100 : 0;
        return (
          <div className="severity-row" key={name}>
            <span className="severity-name">{name}</span>
            <div className="severity-track">
              {count > 0 && (
                <div className={`severity-bar sev-${name}`} style={{ width: `${pct}%` }} />
              )}
            </div>
            <span className="severity-count">{count}</span>
          </div>
        );
      })}
    </div>
  );
}

export function SeverityBadge({ severity }) {
  return (
    <span className={`badge ${severity}`}>
      <span className="swatch" aria-hidden="true" />
      {severity}
    </span>
  );
}

export default function Findings({ job }) {
  const [limit, setLimit] = useState(25);
  const problems = job?.problems ?? [];

  return (
    <div className="card">
      <header>
        <h2>Problems detected ({problems.length})</h2>
        <span className="hint">
          {Object.entries(job?.problems_by_kind ?? {})
            .map(([kind, count]) => `${kind} ${count}`)
            .join(" · ")}
        </span>
      </header>

      <SeverityBreakdown counts={job?.problems_by_severity} />

      {problems.length > 0 && (
        <div className="table-scroll" style={{ marginTop: 14 }}>
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Location</th>
                <th>Code</th>
                <th>Problem</th>
                <th>Detector</th>
              </tr>
            </thead>
            <tbody>
              {problems.slice(0, limit).map((problem, index) => (
                <tr key={`${problem.file}-${problem.line}-${problem.code}-${index}`}>
                  <td><SeverityBadge severity={problem.severity} /></td>
                  <td className="mono">{problem.file}:{problem.line}</td>
                  <td className="mono">{problem.code}</td>
                  <td className="wrap">{problem.message}</td>
                  <td className="mono">{problem.detector}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {problems.length > limit && (
            <button
              className="icon-button"
              style={{ marginTop: 10 }}
              onClick={() => setLimit((value) => value + 50)}
            >
              Show more ({problems.length - limit} hidden)
            </button>
          )}
        </div>
      )}
    </div>
  );
}
