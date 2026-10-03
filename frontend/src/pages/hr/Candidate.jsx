import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, openProtectedFile } from "../../api.js";
import {
  Empty, ErrorBox, Loading, RecBadge, ScoreBar, Spinner, StageBadge, Timeline, fmtDate, money, timeAgo, useAction, useData,
} from "../../components/ui.jsx";

const LIVE_STAGES = ["assessment_sent", "interview_sent"];

export default function Candidate() {
  const { id } = useParams();
  const { data: c, error, loading, reload } = useData(() => api.get(`/api/hr/candidates/${id}`), [id], 4000);
  const [tab, setTab] = useState("overview");
  const action = useAction();

  if (loading && !c) return <Loading />;
  if (!c) return <ErrorBox error={error} />;

  const live = LIVE_STAGES.includes(c.stage);
  return (
    <>
      <div className="page-head">
        <div>
          <Link to={`/hr/jobs/${c.job_id}`} className="small">← {c.job_title}</Link>
          <h1 style={{ marginTop: 6 }}>{c.name}</h1>
          <div className="row">
            <StageBadge stage={c.stage} />
            <RecBadge rec={c.final_report?.recommendation} />
            {live && <span className="badge bad"><span className="dot pulse" /> Live monitoring</span>}
            <span className="faint">{c.email}{c.phone ? ` · ${c.phone}` : ""} · applied {timeAgo(c.created_at)}</span>
          </div>
        </div>
        <div className="row">
          <button className="btn" onClick={() => action.run(() => openProtectedFile(`/api/hr/candidates/${id}/resume`))}>📄 Resume</button>
          {c.offer && <button className="btn" onClick={() => action.run(() => openProtectedFile(`/api/hr/candidates/${id}/offer.pdf`))}>📨 Offer PDF</button>}
        </div>
      </div>
      <ErrorBox error={action.error} />
      {c.last_error && (
        <div className="alert bad row-between">
          <span>⚠️ Agent step failed: {c.last_error}</span>
          {c.can_retry && <button className="btn btn-sm" onClick={() => action.run(async () => { await api.post(`/api/hr/candidates/${id}/retry`); await reload(); })}>Retry step</button>}
        </div>
      )}
      {c.agent_running && !c.last_error && <div className="alert info row"><Spinner /> An agent is processing this candidate...</div>}

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        {[
          ["Resume match", c.score],
          ["Assessment", c.assessment_score],
          ["AI interview", c.interview_score],
          ["Integrity", c.integrity?.score],
        ].map(([l, v]) => (
          <div key={l} className="card stat">
            <div className="label">{l}</div>
            <div className="value" style={{ fontSize: 24 }}>{v == null ? "-" : Math.round(v)}</div>
            <ScoreBar value={v} />
          </div>
        ))}
      </div>

      {c.waiting_for === "hr_decision" && <HRDecision c={c} onDone={reload} />}
      {c.waiting_for === "offer_approval" && <OfferReview key={JSON.stringify(c.offer)} c={c} onDone={reload} />}

      <div className="split">
        <div className="card">
          <div className="tabs">
            {[["overview", "Overview"], ["assessment", "Assessment"], ["proctor", `Proctoring${live ? " 🔴" : ""}`], ["interview", "Interview"], ["resume", "Resume"], ["offer", "Offer"]].map(([k, l]) => (
              <button key={k} className={`tab${tab === k ? " active" : ""}`} onClick={() => setTab(k)}>{l}</button>
            ))}
          </div>
          {tab === "overview" && <Overview c={c} />}
          {tab === "assessment" && <AssessmentResult c={c} />}
          {tab === "proctor" && <ProctorMonitor id={id} live={live} />}
          {tab === "interview" && <InterviewTab c={c} />}
          {tab === "resume" && <pre className="copybox" style={{ maxHeight: 600 }}>{c.resume_text}</pre>}
          {tab === "offer" && <OfferTab c={c} />}
        </div>
        <div className="card">
          <h2>Candidate timeline</h2>
          <Timeline events={c.activity} />
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- HR decision (human-in-the-loop)
function HRDecision({ c, onDone }) {
  const r = c.final_report || {};
  const [note, setNote] = useState("");
  const action = useAction();
  const send = (act) => action.run(async () => { await api.post(`/api/hr/candidates/${c.id}/decision`, { action: act, feedback: note }); await onDone(); });
  return (
    <div className="card" style={{ borderColor: "var(--accent)", marginBottom: 16 }}>
      <div className="card-title">
        <h2>🧑‍⚖️ Your decision: should we make an offer?</h2>
        <RecBadge rec={r.recommendation} />
      </div>
      <p style={{ fontSize: 15.5 }}><b>{r.headline}</b></p>
      <p className="muted">{r.summary}</p>
      {r.integrity_level === "suspicious" && <div className="alert bad">⚠️ Proctoring flagged suspicious behaviour. Review the Proctoring tab before deciding.</div>}
      <div className="grid grid-2">
        <div><b className="small">Strengths</b><ul className="small">{r.strengths?.map((s, i) => <li key={i}>{s}</li>)}</ul></div>
        <div><b className="small">Risks</b><ul className="small">{r.risks?.map((s, i) => <li key={i}>{s}</li>)}</ul></div>
      </div>
      <p className="small"><b>Suggested next step:</b> {r.suggested_next_step}</p>
      <p className="small muted">Weighted overall: <b>{r.scores?.overall}</b> (20% resume · 30% assessment · 50% interview)</p>
      <div className="field">
        <label htmlFor="note">Note (optional; teaches the agents your preferences)</label>
        <input id="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Strong system design, communication is fine for this role" />
      </div>
      <ErrorBox error={action.error} />
      <div className="row">
        <button className="btn btn-ok" disabled={action.busy} onClick={() => send("approve")}>✓ Approve: draft offer</button>
        <button className="btn btn-danger" disabled={action.busy} onClick={() => send("reject")}>✕ Reject</button>
      </div>
    </div>
  );
}

function OfferReview({ c, onDone }) {
  const o = c.offer || {};
  const [edits, setEdits] = useState({ annual_ctc: o.annual_ctc, joining_date: o.joining_date, probation: o.probation, letter_body: o.letter_body });
  const [feedback, setFeedback] = useState("");
  const action = useAction();
  const send = (act) => action.run(async () => { await api.post(`/api/hr/candidates/${c.id}/offer-decision`, { action: act, feedback, edits }); await onDone(); });
  const set = (k) => (e) => setEdits({ ...edits, [k]: e.target.value });
  return (
    <div className="card" style={{ borderColor: "var(--accent)", marginBottom: 16 }}>
      <div className="card-title">
        <h2>📨 Approve the offer before it is sent</h2>
        <button className="btn btn-sm" onClick={() => action.run(() => openProtectedFile(`/api/hr/candidates/${c.id}/offer.pdf`))}>Preview PDF</button>
      </div>
      <div className="alert info small"><b>Why this CTC (AI):</b> {o.justification}</div>
      <div className="grid grid-3">
        <div className="field"><label>Annual CTC ({o.currency})</label><input type="number" value={edits.annual_ctc} onChange={set("annual_ctc")} /></div>
        <div className="field"><label>Joining date</label><input type="date" value={edits.joining_date} onChange={set("joining_date")} /></div>
        <div className="field"><label>Probation</label><input value={edits.probation} onChange={set("probation")} /></div>
      </div>
      <div className="field"><label>Letter body</label><textarea rows={6} value={edits.letter_body} onChange={set("letter_body")} /></div>
      <div className="field"><label>Feedback for AI redraft (optional)</label><input value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="e.g. Add a 50,000 joining bonus line" /></div>
      <ErrorBox error={action.error} />
      <div className="row">
        <button className="btn btn-ok" disabled={action.busy} onClick={() => send("approve")}>✓ Approve & send to candidate</button>
        <button className="btn" disabled={action.busy || !feedback.trim()} onClick={() => send("redraft")}>Ask AI to redraft</button>
        <button className="btn btn-danger" disabled={action.busy} onClick={() => confirm("Withdraw and reject this candidate?") && send("cancel")}>Withdraw</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- tabs
function Overview({ c }) {
  const s = c.screening;
  const p = c.parsed;
  if (!s) return <Empty icon="🔎" title="Resume is being screened..." />;
  return (
    <div className="stack">
      <div>
        <h3>Resume screening: {s.total}/100</h3>
        <p className="small muted">{s.reasoning}</p>
        <div className="grid grid-2 small">
          {Object.entries(s.breakdown || {}).map(([k, v]) => (
            <div key={k}><span className="muted">{k.replace("_", " ")}{k === "semantic" ? ` (${s.semantic_method})` : ""}</span><ScoreBar value={v} /></div>
          ))}
        </div>
      </div>
      <div className="grid grid-2 small">
        <div><b>✅ Must-haves matched</b><div className="chips" style={{ marginTop: 4 }}>{s.must_have_matched?.map((x) => <span key={x} className="badge ok">{x}</span>)}</div></div>
        <div><b>❌ Missing</b><div className="chips" style={{ marginTop: 4 }}>{s.must_have_missing?.map((x) => <span key={x} className="badge bad">{x}</span>)}</div></div>
        <div><b>Strengths</b><ul>{s.strengths?.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
        <div><b>Gaps</b><ul>{s.gaps?.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
      </div>
      {p && (
        <div className="small">
          <h3>Parsed profile</h3>
          <p>{p.summary}</p>
          <p><b>Experience:</b> {p.total_experience_years} yrs · <b>Current:</b> {p.current_role}</p>
          <div className="chips">{p.skills?.map((x) => <span key={x} className="chip">{x}</span>)}</div>
          {p.projects?.length > 0 && <><b>Projects</b><ul>{p.projects.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
          {p.education?.length > 0 && <p><b>Education:</b> {p.education.join("; ")}</p>}
        </div>
      )}
      <p className="faint">Scored on anonymised resume text (name, email, phone and links removed) for fairness.</p>
    </div>
  );
}

function AssessmentResult({ c }) {
  const r = c.assessment_result;
  if (!r) return <Empty icon="📝" title={c.stage === "assessment_sent" ? (c.assessment_started_at ? "Candidate is taking the test now..." : "Waiting for candidate to start") : "No assessment yet"} />;
  const sub = c.assessment_submission || {};
  return (
    <div className="stack small">
      <div className="row">
        <span className="badge info">Overall {r.overall}%</span>
        <span className="badge">MCQ {r.mcq_correct}/{r.mcq_total}</span>
        {r.coding_pct != null && <span className="badge">Coding {r.coding_pct}%</span>}
        <span className={`badge ${r.integrity?.level === "clean" ? "ok" : r.integrity?.level === "suspicious" ? "bad" : "warn"}`}>Integrity {r.integrity?.score}</span>
        <span className="faint">Took {sub.elapsed_minutes} min{sub.auto_submitted ? " (auto-submitted at time-up)" : ""}</span>
      </div>
      {r.coding?.map((cr, i) => (
        <div key={i}>
          <h3>💻 {cr.title}: {cr.passed}/{cr.total} tests · score {cr.score}</h3>
          {cr.review?.correctness != null && (
            <p>Correctness {cr.review.correctness}/10 · Quality {cr.review.quality}/10 · AI-generated likelihood{" "}
              <span className={`badge ${cr.review.ai_generated_likelihood === "high" ? "bad" : cr.review.ai_generated_likelihood === "medium" ? "warn" : "ok"}`}>{cr.review.ai_generated_likelihood}</span></p>
          )}
          <p className="muted">{cr.review?.feedback}</p>
          <pre className="copybox">{sub.coding?.[String(i)]?.code || "(no code)"}</pre>
        </div>
      ))}
    </div>
  );
}

const SEV = { high: "bad", medium: "warn", low: "", info: "info" };
const LABEL = {
  tab_hidden: "Switched tab / minimised", window_blur: "Left the test window", fullscreen_exit: "Exited full-screen",
  paste: "Pasted text", large_paste: "Large paste", copy: "Copied text", no_face: "No face visible", multiple_faces: "Multiple faces",
  looking_away: "Looking away", phone_detected: "Phone detected", voice_detected: "Voices / talking", camera_off: "Camera off",
  devtools: "DevTools opened", snapshot: "Periodic snapshot", session_start: "Session started", right_click: "Right-click",
};

function ProctorMonitor({ id, live }) {
  const [events, setEvents] = useState([]);
  const [integrity, setIntegrity] = useState(null);
  const lastId = useRef(0);

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const res = await api.get(`/api/hr/candidates/${id}/proctor?since_id=${lastId.current}`);
        if (stop) return;
        if (res.events.length) {
          lastId.current = res.events[res.events.length - 1].id;
          setEvents((prev) => [...prev, ...res.events]);
        }
        setIntegrity(res.integrity);
      } catch { /* retry next tick */ }
    };
    load();
    const t = setInterval(load, live ? 2500 : 15000);
    return () => { stop = true; clearInterval(t); };
  }, [id, live]);

  const flags = events.filter((e) => e.type !== "snapshot" && e.type !== "session_start");
  const shots = events.filter((e) => e.snapshot).slice(-24).reverse();
  return (
    <div className="stack">
      <div className="row">
        {integrity && <span className={`badge ${integrity.level === "clean" ? "ok" : integrity.level === "suspicious" ? "bad" : "warn"}`}>Integrity {integrity.score} · {integrity.level.replace("_", " ")}</span>}
        {live && <span className="badge bad"><span className="dot pulse" /> live, refreshing every 2.5s</span>}
        <span className="faint">AI flags are evidence for human review, never an automatic rejection.</span>
      </div>
      {integrity?.counts && Object.keys(integrity.counts).length > 0 && (
        <div className="chips">{Object.entries(integrity.counts).filter(([k]) => k !== "snapshot").map(([k, n]) => <span key={k} className="chip">{LABEL[k] || k}: {n}</span>)}</div>
      )}
      {shots.length > 0 && (
        <div>
          <h3>Webcam snapshots</h3>
          <div className="snapshot-grid">
            {shots.map((e) => (
              <figure key={e.id}>
                <img src={e.snapshot} alt={e.type} className={e.type !== "snapshot" ? "flag" : ""} />
                <figcaption>{LABEL[e.type] || e.type} · {new Date(e.created_at).toLocaleTimeString()}</figcaption>
              </figure>
            ))}
          </div>
        </div>
      )}
      <div>
        <h3>Event log</h3>
        {flags.length === 0 ? <p className="muted small">No suspicious events.</p> : (
          <div className="table-wrap" style={{ maxHeight: 360, overflow: "auto" }}>
            <table>
              <thead><tr><th>Time</th><th>Phase</th><th>Event</th><th>Severity</th><th>Detail</th></tr></thead>
              <tbody>
                {[...flags].reverse().map((e) => (
                  <tr key={e.id}>
                    <td className="faint">{new Date(e.created_at).toLocaleTimeString()}</td>
                    <td className="small">{e.phase}</td>
                    <td className="small">{LABEL[e.type] || e.type}</td>
                    <td><span className={`badge ${SEV[e.severity]}`}>{e.severity}</span></td>
                    <td className="small muted">{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function InterviewTab({ c }) {
  const ev = c.interview_eval;
  if (!c.interview_transcript.length) return <Empty icon="🎙️" title="Interview not started yet" />;
  return (
    <div className="stack">
      {ev && (
        <div className="small">
          <div className="row" style={{ marginBottom: 8 }}>
            <RecBadge rec={ev.recommendation} /><span className="badge info">Overall {ev.overall}/100</span>
          </div>
          <div className="grid grid-4">
            {["technical", "communication", "problem_solving", "culture_fit"].map((k) => (
              <div key={k}><span className="muted">{k.replace("_", " ")}</span><ScoreBar value={ev[k]} max={10} /></div>
            ))}
          </div>
          <p style={{ marginTop: 10 }}>{ev.summary}</p>
          <div className="grid grid-2">
            <div><b>Strengths</b><ul>{ev.strengths?.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
            <div><b>Concerns</b><ul>{ev.concerns?.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
          </div>
        </div>
      )}
      <h3>Transcript</h3>
      <div className="chat" style={{ maxHeight: 500 }}>
        {c.interview_transcript.map((t, i) => (
          <div key={i} className={`bubble ${t.role === "interviewer" ? "ai" : "me"}`}>
            <div className="faint">{t.role === "interviewer" ? `🤖 Interviewer${t.topic ? ` · ${t.topic}` : ""}` : `🧑 ${c.name}`}</div>
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function OfferTab({ c }) {
  const o = c.offer;
  if (!o) return <Empty icon="📨" title="No offer yet" />;
  return (
    <div className="small stack">
      <div className="grid grid-2">
        <div><b>Position:</b> {o.position}</div>
        <div><b>CTC:</b> {money(o.annual_ctc, o.currency)}</div>
        <div><b>Joining:</b> {o.joining_date}</div>
        <div><b>Valid until:</b> {o.valid_until}</div>
      </div>
      <p style={{ whiteSpace: "pre-wrap" }}>{o.letter_body}</p>
      {c.candidate_response && (
        <div className={`alert ${c.candidate_response.action === "accept" ? "ok" : "bad"}`}>
          Candidate {c.candidate_response.action}ed on {fmtDate(c.candidate_response.at)}
          {c.candidate_response.reason ? `: "${c.candidate_response.reason}"` : ""}
        </div>
      )}
    </div>
  );
}
