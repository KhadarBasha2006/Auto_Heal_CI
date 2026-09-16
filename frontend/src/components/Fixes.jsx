import React from "react";

export default function Fixes({ job }) {
  const fixes = job?.fixes ?? [];
  return (
    <div className="card">
      <header>
        <h2>Fixes applied ({fixes.length})</h2>
        <span className="hint">
          rule-based repairs run first; model repairs are verified before they are kept
        </span>
      </header>
      {fixes.length === 0 ? (
        <p className="empty">No fixes applied yet.</p>
      ) : (
        <ul className="fix-list">
          {fixes.map((fix, index) => (
            <li className={`fix-item ${fix.tier === "ai" ? "ai" : ""}`} key={index}>
              <div className="fix-head">
                <span className="fix-file">{fix.file}</span>
                <span className="tier-tag">{fix.tier}</span>
                <span className="tier-tag">round {fix.round}</span>
              </div>
              <div className="fix-desc">{fix.description}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
