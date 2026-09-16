import React, { useCallback, useEffect, useRef, useState } from "react";
import AnalyzeForm from "./components/AnalyzeForm.jsx";
import Progress from "./components/Progress.jsx";
import StatTiles from "./components/StatTiles.jsx";
import Findings from "./components/Findings.jsx";
import Fixes from "./components/Fixes.jsx";
import Validation from "./components/Validation.jsx";
import Diagnosis from "./components/Diagnosis.jsx";
import ScoreCard from "./components/ScoreCard.jsx";
import ActivityLog from "./components/ActivityLog.jsx";
import ResultPanel from "./components/ResultPanel.jsx";
import { PipelineHealth, RepoHealth } from "./components/HealthPanels.jsx";
import Background3D from "./components/Background3D.jsx";
import { getHealth, startAnalysis, subscribeToJob } from "./api.js";

const TERMINAL = ["succeeded", "partial", "failed", "cancelled"];
const WORKFLOW_STEPS = [
  {
    name: "Detect",
    description: "Clones the repo and scans for syntax errors, bad indentation, unresolved imports, type and lint defects, and malformed workflow files.",
  },
  {
    name: "Diagnose",
    description: "Reads Actions telemetry and the provider status page to classify failures as code-level or platform-level, with the evidence shown.",
  },
  {
    name: "Heal",
    description: "Applies deterministic repairs first, then model-generated ones; every model patch must parse and reduce the problem count or it is rolled back.",
  },
  {
    name: "Validate",
    description: "Runs syntax, imports, lint, compile, and test gates, looping until they pass or no further repair is possible.",
  },
  {
    name: "Communicate",
    description: "Pushes a branch and writes a post-incident report with the root cause and what was changed.",
  },
];

const PREFERS_REDUCED_MOTION = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * Reveal cards as they scroll into view.
 *
 * Re-runs whenever `dep` changes because panels appear mid-run as the job
 * streams events. Each element is unobserved once revealed, so a card that is
 * updating live never re-animates.
 */
function useScrollReveal(dep) {
  useEffect(() => {
    // Select by :not(.revealed), NOT :not(.reveal). This effect re-runs on
    // every streamed update and disconnects the previous observer; selecting
    // on .reveal would skip cards that were already marked but had not yet
    // scrolled into view, leaving them observed by nobody and invisible for
    // good.
    const cards = document.querySelectorAll(".card:not(.revealed)");
    if (!cards.length) return undefined;

    const revealAll = () =>
      document
        .querySelectorAll(".card.reveal:not(.revealed)")
        .forEach((el) => el.classList.add("revealed"));

    // Nothing may depend on JS to become visible at all. If motion is
    // unwanted or the observer is unavailable, show everything outright.
    if (PREFERS_REDUCED_MOTION() || !("IntersectionObserver" in window)) {
      cards.forEach((el) => el.classList.add("reveal", "revealed"));
      return undefined;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("revealed");
            observer.unobserve(entry.target);
          }
        });
      },
      // Positive bottom margin starts the reveal just before a card scrolls
      // into view, so it settles rather than popping.
      { threshold: 0.01, rootMargin: "0px 0px 12% 0px" },
    );

    cards.forEach((el, index) => {
      if (!el.classList.contains("reveal")) {
        el.classList.add("reveal");
        // Stagger only the first screenful; later cards should not feel laggy.
        el.style.setProperty("--reveal-delay", `${Math.min(index, 5) * 55}ms`);
      }
      observer.observe(el);
    });

    // Failsafe: whatever has not revealed after this point is shown anyway.
    // A decorative animation must never be the reason content stays hidden --
    // from a stalled observer, a print job, or a page capture.
    const failsafe = window.setTimeout(revealAll, 8000);
    window.addEventListener("beforeprint", revealAll);

    return () => {
      observer.disconnect();
      window.clearTimeout(failsafe);
      window.removeEventListener("beforeprint", revealAll);
    };
  }, [dep]);
}

/** Condense the sticky masthead once the page has scrolled past its height. */
function useCondensedHeader(threshold = 40) {
  const [condensed, setCondensed] = useState(false);
  useEffect(() => {
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        setCondensed(window.scrollY > threshold);
        frame = 0;
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [threshold]);
  return condensed;
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [job, setJob] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);

  const unsubscribeRef = useRef(null);
  const startedAtRef = useRef(null);

  const condensed = useCondensedHeader();
  // Keyed on the panels that can appear mid-run, so new cards get observed.
  useScrollReveal(
    `${job?.job_id ?? "idle"}:${job?.status ?? ""}:${job?.fixes_applied ?? 0}:` +
      `${job?.validations?.length ?? 0}:${job?.score?.total ?? ""}`,
  );

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  // Local ticker so the elapsed readout advances smoothly between events.
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => {
      if (startedAtRef.current) {
        setElapsed((Date.now() - startedAtRef.current) / 1000);
      }
    }, 100);
    return () => clearInterval(timer);
  }, [running]);

  useEffect(() => () => unsubscribeRef.current?.(), []);

  const applySnapshot = useCallback((snapshot) => {
    setJob(snapshot);
    if (typeof snapshot.elapsed_seconds === "number" && TERMINAL.includes(snapshot.status)) {
      setElapsed(snapshot.elapsed_seconds);
    }
    if (TERMINAL.includes(snapshot.status)) {
      setRunning(false);
    }
  }, []);

  // Incremental events keep the UI live without refetching the whole snapshot.
  const applyEvent = useCallback((event) => {
    const { type, data } = event;
    setJob((current) => {
      if (!current) return current;
      const next = { ...current };
      switch (type) {
        case "log":
          next.logs = [...(current.logs ?? []), data];
          break;
        case "phase":
          next.phase = data.phase;
          next.progress = data.progress;
          next.status = data.status;
          break;
        case "progress":
          next.progress = data.progress;
          break;
        case "problems":
          next.problems = [...(current.problems ?? []), ...data.added];
          next.problems_found = data.total;
          next.problems_by_severity = data.by_severity;
          break;
        case "fix":
          next.fixes = [...(current.fixes ?? []), data.fix];
          next.fixes_applied = data.total_fixes;
          break;
        case "validation":
          next.validations = [...(current.validations ?? []), data];
          next.validation_passed = data.passed;
          break;
        case "diagnosis":
          next.diagnosis = data;
          break;
        case "remediation":
          next.remediations = [...(current.remediations ?? []), data];
          break;
        case "pipeline_health":
          next.pipeline_health = data;
          break;
        case "repo_health":
          next.repo_health = data;
          break;
        case "score":
          next.score = data;
          break;
        default:
          return current;
      }
      return next;
    });
  }, []);

  const handleStart = useCallback(
    async (payload) => {
      setError(null);
      setJob(null);
      setElapsed(0);
      setRunning(true);
      startedAtRef.current = Date.now();
      unsubscribeRef.current?.();

      try {
        const accepted = await startAnalysis(payload);
        setJob({
          job_id: accepted.job_id,
          status: accepted.status,
          phase: "queued",
          progress: 0,
          branch_name: payload.branch_name,
          logs: [],
          problems: [],
          fixes: [],
          validations: [],
          remediations: [],
        });
        unsubscribeRef.current = subscribeToJob(accepted.job_id, {
          onSnapshot: applySnapshot,
          onEvent: applyEvent,
          onError: (streamError) => setError(streamError.message),
        });
      } catch (startError) {
        setError(startError.message);
        setRunning(false);
      }
    },
    [applyEvent, applySnapshot],
  );

  const aiEnabled = health?.checks?.ai_repair_tier;

  return (
    <>
      <Background3D />

      <div className="app">
      <div className={`masthead${condensed ? " condensed" : ""}`}>
        <div>
          <h1>Autonomous CI/CD Healing Agent</h1>
          <div className="workflow-overview" aria-label="Healing workflow">
            {WORKFLOW_STEPS.map((step, index) => (
              <button
                className={`workflow-step${selectedWorkflow === index ? " selected" : ""}`}
                key={step.name}
                type="button"
                aria-pressed={selectedWorkflow === index}
                onClick={() => setSelectedWorkflow(selectedWorkflow === index ? null : index)}
              >
                <span className="dot workflow-dot" aria-hidden="true" />
                <strong>{step.name}</strong>
              </button>
            ))}
          </div>
          {selectedWorkflow !== null && (
            <p className="workflow-description">
              {WORKFLOW_STEPS[selectedWorkflow].description}
            </p>
          )}
        </div>
        <div className="masthead-actions">
          {health && (
            <span className={`pill ${health.status === "ok" ? "good" : "warn"}`}>
              <span className="dot" />
              API {health.status} · v{health.version}
            </span>
          )}
          <span className={`pill ${aiEnabled ? "good" : "muted"}`}>
            <span className="dot" />
            {aiEnabled ? "AI repairs on" : "rule-based only"}
          </span>
        </div>
      </div>

      {!health && (
        <div className="banner warn">
          Cannot reach the backend. Start it with{" "}
          <span className="mono">uvicorn healing_agent.app:app --port 8000</span>{" "}
          (from the <span className="mono">backend/</span> directory), or set{" "}
          <span className="mono">VITE_API_BASE_URL</span>.
        </div>
      )}

      {error && <div className="banner error">{error}</div>}

      <div className={`layout${job ? "" : " layout-idle"}`}>
        <div className="run-agent-column">
          <AnalyzeForm onStart={handleStart} running={running} />
          {job && <Progress job={job} elapsed={elapsed} />}
          {job && <ActivityLog logs={job.logs} />}
        </div>

        {job && (
          <div>
            <>
              <StatTiles job={job} elapsed={elapsed} />
              <ResultPanel job={job} />
              <ScoreCard job={job} />
              <Diagnosis job={job} />
              <Validation job={job} />
              <Findings job={job} />
              <Fixes job={job} />
              <PipelineHealth job={job} />
              <RepoHealth job={job} />
            </>
          </div>
        )}
      </div>

      <div className="footer">
        <a href="https://github.com/KhadarBasha2006/Auto_Heal_CI" target="_blank" rel="noreferrer">
          Autonomous CI/CD Healing Agent
        </a>
        <span>
          <a href="https://github.com/KhadarBasha2006" target="_blank" rel="noreferrer">
            @KhadarBasha2006
          </a>
        </span>
      </div>
      </div>
    </>
  );
}
