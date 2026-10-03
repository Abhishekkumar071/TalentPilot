import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

export function Spinner() {
  return <span className="spinner" aria-label="Loading" />;
}

export function Loading({ text = "Loading..." }) {
  return (
    <div className="center">
      <Spinner />
      <span>{text}</span>
    </div>
  );
}

export function ErrorBox({ error }) {
  if (!error) return null;
  return <div className="alert bad">{String(error.message || error)}</div>;
}

export function Empty({ icon = "📭", title, children }) {
  return (
    <div className="empty">
      <div className="big">{icon}</div>
      <h3>{title}</h3>
      {children && <div className="muted">{children}</div>}
    </div>
  );
}

export function Markdown({ children }) {
  return (
    <div className="markdown">
      <ReactMarkdown>{children || ""}</ReactMarkdown>
    </div>
  );
}

// ---------------------------------------------------------------- data hooks
/** Load data and optionally re-poll every `interval` ms (pauses when tab hidden). */
export function useData(loader, deps = [], interval = 0) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const reload = useCallback(async () => {
    try {
      const d = await loaderRef.current();
      setData(d);
      setError(null);
      return d;
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    reload();
    if (!interval) return undefined;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, interval);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload, setData };
}

/** Wrap an async action with busy/error state. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const run = useCallback(async (fn) => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e);
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, setError };
}

// ---------------------------------------------------------------- formatting
export function timeAgo(iso) {
  if (!iso) return "";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 0) return "in " + duration(-s);
  if (s < 60) return "just now";
  return duration(s) + " ago";
}

export function timeLeft(iso) {
  if (!iso) return "";
  const s = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  return s <= 0 ? "closed" : duration(s) + " left";
}

function duration(s) {
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "-";
}

export function money(n, currency = "INR") {
  if (n == null) return "-";
  return `${currency} ${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

// ---------------------------------------------------------------- status badges
const JOB_STATUS = {
  drafting: ["info", "Drafting JD", true],
  awaiting_jd_approval: ["accent", "Needs JD approval"],
  publishing: ["info", "Publishing", true],
  collecting: ["ok", "Live · collecting"],
  evaluating: ["info", "Evaluating", true],
  replanning: ["info", "Replanning", true],
  awaiting_replan_approval: ["accent", "Needs replan approval"],
  screening: ["info", "Screening", true],
  in_pipeline: ["info", "Interviewing"],
  needs_attention: ["warn", "Needs attention"],
  filled: ["ok", "Filled ✓"],
  cancelled: ["", "Cancelled"],
  failed: ["bad", "Failed"],
};

const STAGE = {
  applied: ["", "Applied"],
  screened: ["", "Screened"],
  shortlisted: ["info", "Shortlisted"],
  waitlisted: ["", "Waitlisted"],
  assessment_sent: ["info", "Test sent"],
  assessment_done: ["info", "Test done"],
  interview_sent: ["info", "Interview sent"],
  interview_done: ["info", "Interviewed"],
  hr_review: ["accent", "Needs HR decision"],
  offer_review: ["accent", "Offer needs approval"],
  offer_sent: ["warn", "Offer sent"],
  hired: ["ok", "Hired 🎉"],
  offer_declined: ["bad", "Declined offer"],
  rejected: ["bad", "Not progressed"],
};

export function JobStatus({ status }) {
  const [tone, label, live] = JOB_STATUS[status] || ["", status];
  return (
    <span className={`badge ${tone}`}>
      {live && <span className="dot pulse" />}
      {label}
    </span>
  );
}

export function StageBadge({ stage }) {
  const [tone, label] = STAGE[stage] || ["", stage];
  return <span className={`badge ${tone}`}>{label}</span>;
}

const REC = {
  strong_hire: ["ok", "Strong hire"],
  hire: ["ok", "Hire"],
  lean_no: ["warn", "Lean no"],
  no_hire: ["bad", "No hire"],
};

export function RecBadge({ rec }) {
  if (!rec) return null;
  const [tone, label] = REC[rec] || ["", rec];
  return <span className={`badge ${tone}`}>{label}</span>;
}

export function ScoreBar({ value, max = 100, label }) {
  if (value == null) return <span className="faint">-</span>;
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const tone = pct >= 70 ? "ok" : pct >= 50 ? "" : pct >= 35 ? "warn" : "bad";
  return (
    <div className="score" title={label}>
      <div className={`bar ${tone}`}>
        <span style={{ width: `${pct}%` }} />
      </div>
      <b className="small">{Math.round(value)}</b>
    </div>
  );
}

const ACTOR_ICON = {
  hr: "🧑‍💼", candidate: "🧑‍🎓", system: "⚙️", "job-agent": "🤖", replanner: "🔁",
  "screening-agent": "🔎", "assessment-agent": "📝", "interview-agent": "🎙️",
  "report-agent": "📊", "offer-agent": "📨", "pipeline-agent": "🧭",
};

export function Timeline({ events }) {
  if (!events?.length) return <p className="muted small">No activity yet.</p>;
  return (
    <ul className="timeline">
      {events.map((e) => (
        <li key={e.id}>
          <span className="ico">{e.kind === "error" ? "⚠️" : e.kind === "action_needed" ? "👉" : ACTOR_ICON[e.actor] || "•"}</span>
          <div>
            <div className={e.kind === "error" ? "small" : "small"} style={e.kind === "error" ? { color: "var(--bad)" } : undefined}>
              {e.message}
            </div>
            <div className="meta">
              {e.actor} · {timeAgo(e.created_at)}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function CopyButton({ text, label = "Copy" }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn btn-sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard blocked */
        }
      }}
    >
      {done ? "Copied ✓" : label}
    </button>
  );
}

export function Modal({ title, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card modal" role="dialog" aria-modal="true">
        <div className="card-title">
          <h2>{title}</h2>
          <button className="btn btn-sm" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
