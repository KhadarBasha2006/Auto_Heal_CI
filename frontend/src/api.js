// Backend client. Empty base URL means same-origin, which is what the Vercel
// rewrite provides in production and what the Vite dev proxy provides locally.
const BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

export const apiUrl = (path) => `${BASE}${path}`;

async function request(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    const detail =
      body?.detail ?? body?.error ?? `Request failed (${response.status})`;
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return body;
}

export const getHealth = () => request("/api/health");
export const getPlatformHealth = () => request("/api/health/platform");
export const getJob = (jobId) => request(`/api/jobs/${jobId}`);

export const startAnalysis = (payload) =>
  request("/api/analyze", { method: "POST", body: JSON.stringify(payload) });

export async function getReport(jobId) {
  const response = await fetch(apiUrl(`/api/jobs/${jobId}/report`));
  if (!response.ok) throw new Error("Report not available yet");
  return response.text();
}

/**
 * Subscribe to a job's live event stream.
 *
 * Server-Sent Events are the primary channel. Some hosting layers buffer
 * streamed responses, which would leave the dashboard frozen on a job that is
 * actually progressing, so a polling fallback takes over if the stream fails
 * or goes quiet. Both paths deliver the same snapshot shape.
 */
export function subscribeToJob(jobId, { onSnapshot, onEvent, onError }) {
  let closed = false;
  let source = null;
  let pollTimer = null;
  let lastActivity = Date.now();

  const stopPolling = () => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const startPolling = () => {
    if (closed || pollTimer) return;
    pollTimer = setInterval(async () => {
      if (closed) return;
      try {
        const snapshot = await getJob(jobId);
        onSnapshot?.(snapshot);
        if (["succeeded", "failed", "partial", "cancelled"].includes(snapshot.status)) {
          close();
        }
      } catch (error) {
        onError?.(error);
      }
    }, 1500);
  };

  const close = () => {
    closed = true;
    stopPolling();
    if (source) {
      source.close();
      source = null;
    }
  };

  try {
    source = new EventSource(apiUrl(`/api/jobs/${jobId}/events`));

    source.onmessage = () => {
      lastActivity = Date.now();
    };

    ["snapshot", "log", "phase", "progress", "problems", "fix", "validation",
     "diagnosis", "remediation", "pipeline_health", "repo_health", "score",
     "heartbeat", "done"].forEach((name) => {
      source.addEventListener(name, (message) => {
        lastActivity = Date.now();
        let payload;
        try {
          payload = JSON.parse(message.data);
        } catch {
          return;
        }
        if (name === "snapshot") onSnapshot?.(payload.data);
        else onEvent?.(payload);
        if (name === "done") {
          onSnapshot?.(payload.data);
          close();
        }
      });
    });

    source.onerror = () => {
      // The stream died or was never established; fall back to polling so the
      // dashboard keeps updating rather than silently stalling.
      if (!closed) startPolling();
    };
  } catch {
    startPolling();
  }

  // If the stream connects but delivers nothing for a while, assume it is
  // being buffered upstream and poll alongside it.
  const watchdog = setInterval(() => {
    if (closed) {
      clearInterval(watchdog);
      return;
    }
    if (Date.now() - lastActivity > 20000) startPolling();
  }, 5000);

  return () => {
    clearInterval(watchdog);
    close();
  };
}
