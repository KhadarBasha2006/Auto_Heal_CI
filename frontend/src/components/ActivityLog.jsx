import React, { useEffect, useRef } from "react";

export default function ActivityLog({ logs }) {
  const listRef = useRef(null);
  const entries = logs ?? [];

  // Scroll the log's own container rather than calling scrollIntoView, which
  // walks up and scrolls the window too -- that yanked the whole page down on
  // every streamed line.
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    // Leave the user alone if they have scrolled up to read earlier output.
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  return (
    <div className="card">
      <header>
        <h2>Activity log</h2>
        <span className="hint">{entries.length} entries</span>
      </header>
      {entries.length === 0 ? (
        <p className="empty">Waiting for the agent to start…</p>
      ) : (
        <div className="log" ref={listRef}>
          {entries.map((entry, index) => (
            <div className={`log-line ${entry.level}`} key={index}>
              <span className="log-time">
                {new Date(entry.ts * 1000).toLocaleTimeString([], {
                  hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit",
                })}
              </span>
              <span className="log-msg">{entry.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
