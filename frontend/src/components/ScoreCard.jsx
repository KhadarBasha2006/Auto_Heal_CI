import React from "react";

const GRADE_COLOR = (grade) => {
  if (!grade) return "var(--text-muted)";
  if (grade.startsWith("A")) return "var(--status-good)";
  if (grade.startsWith("B")) return "var(--accent)";
  if (grade.startsWith("C")) return "var(--status-warning)";
  if (grade.startsWith("D")) return "var(--status-serious)";
  return "var(--status-critical)";
};

export default function ScoreCard({ job }) {
  const score = job?.score;
  if (!score || score.total == null) return null;

  const color = GRADE_COLOR(score.grade);
  const breakdownTotal = (score.breakdown ?? []).reduce(
    (sum, item) => sum + item.points,
    0,
  );
  const displayBreakdown = (score.breakdown ?? []).map((item) => ({ ...item }));
  if (breakdownTotal > score.total && breakdownTotal > 0) {
    const fractions = displayBreakdown.map((item) => {
      const exact = (item.points * score.total) / breakdownTotal;
      item.points = Math.floor(exact);
      return exact - item.points;
    });
    let remainder = score.total - displayBreakdown.reduce((sum, item) => sum + item.points, 0);
    fractions
      .map((fraction, index) => ({ fraction, index }))
      .sort((left, right) => right.fraction - left.fraction)
      .forEach(({ index }) => {
        if (remainder > 0) {
          displayBreakdown[index].points += 1;
          remainder -= 1;
        }
      });
  }

  return (
    <div className="card">
      <header>
        <h2>Run score</h2>
        <span className="hint">weighted by repair impact, gates, speed, and criticals cleared</span>
      </header>
      <div className="score-wrap">
        <div className="score-hero">
          <span className="score-number" style={{ color }}>{score.total}</span>
          <span className="score-max">/ 100</span>
          <span className="score-grade" style={{ color, borderColor: color }}>
            {score.grade}
          </span>
        </div>
        <div className="score-breakdown">
          {displayBreakdown.map((item) => (
            <div className="score-item" key={item.key}>
              <span>{item.label}</span>
              <span className="mono">{item.points}/{item.max}</span>
              <div className="bar-track">
                <div
                  className="bar-fill"
                  style={{ width: `${(item.points / item.max) * 100}%` }}
                />
              </div>
              <span className="detail">{item.detail}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
