import React from "react";

const HEADLINE = {
  code: "Code-level failure",
  platform: "Platform-level failure",
  mixed: "Mixed — code defects during platform degradation",
  unknown: "Inconclusive",
};

function evidenceClass(text) {
  if (text.startsWith("[platform")) return "platform";
  if (text.startsWith("[code")) return "code";
  if (text.startsWith("[rule")) return "rule";
  return "";
}

export default function Diagnosis({ job }) {
  const diagnosis = job?.diagnosis;
  const remediations = job?.remediations ?? [];

  return (
    <div className="card">
      <header>
        <h2>Failure diagnosis</h2>
        <span className="hint">is it your code, or is the platform degraded?</span>
      </header>

      {!diagnosis ? (
        <p className="empty">Diagnosis pending.</p>
      ) : (
        <>
          <div className={`verdict ${diagnosis.failure_class}`}>
            <span className="verdict-class">
              {HEADLINE[diagnosis.failure_class] ?? diagnosis.failure_class}
            </span>
            <span className="pill muted">
              <span className="dot" />
              {Math.round(diagnosis.confidence * 100)}% confidence
            </span>
            <span className="pill muted">
              <span className="dot" />
              {diagnosis.recommended_action.replace(/_/g, " ")}
            </span>
          </div>

          <p style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
            {diagnosis.summary}
          </p>

          {diagnosis.evidence?.length > 0 && (
            <ul className="evidence">
              {diagnosis.evidence.map((item, index) => (
                <li key={index} className={evidenceClass(item)}>{item}</li>
              ))}
            </ul>
          )}

          {remediations.length > 0 && (
            <>
              <h3 style={{ marginTop: 16, marginBottom: 8 }}>Corrective actions</h3>
              <ul className="fix-list">
                {remediations.map((step, index) => (
                  <li className="fix-item" key={index}>
                    <div className="fix-head">
                      <span className="fix-file">{step.action.replace(/_/g, " ")}</span>
                      <span className="tier-tag">
                        {step.executed
                          ? step.succeeded === false ? "executed · failed" : "executed"
                          : "planned"}
                      </span>
                    </div>
                    <div className="fix-desc">{step.description}</div>
                    {step.detail && <div className="fix-desc">{step.detail}</div>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  );
}
