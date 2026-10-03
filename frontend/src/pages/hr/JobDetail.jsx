import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../api.js";
import {
  CopyButton, Empty, ErrorBox, JobStatus, Loading, Markdown, RecBadge, ScoreBar, Spinner, StageBadge, Timeline,
  fmtDate, money, timeLeft, useAction, useData,
} from "../../components/ui.jsx";

const BUSY = ["drafting", "publishing", "evaluating", "replanning", "screening"];

export default function JobDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data: job, error, loading, reload } = useData(() => api.get(`/api/hr/jobs/${id}`), [id], 3500);
  const [tab, setTab] = useState("candidates");
  const action = useAction();

  if (loading && !job) return <Loading />;
  if (!job) return <ErrorBox error={error} />;

  const act = (path, body) => action.run(async () => { await api.post(`/api/hr/jobs/${id}/${path}`, body); await reload(); });
  const working = job.agent_running || BUSY.includes(job.status);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="row" style={{ marginBottom: 6 }}>
            <JobStatus status={job.status} />
            <span className="faint">Job #{job.id} · JD v{job.jd_version} · {job.replan_count} replan(s)</span>
          </div>
          <h1>{job.title}</h1>
          <p className="muted">{[job.department, job.location, job.work_mode, job.employment_type].filter(Boolean).join(" · ")}</p>
        </div>
        <div className="row">
          {job.waiting_for === "deadline" && (
            <>
              <span className="badge ok">⏰ {timeLeft(job.deadline)}</span>
              <button className="btn" disabled={action.busy} onClick={() => act("extend", { hours: 24 })}>+24h</button>
              <button className="btn btn-primary" disabled={action.busy} onClick={() => act("close-applications")}>Close applications & screen now</button>
            </>
          )}
          {job.status === "needs_attention" && (
            <button className="btn btn-primary" disabled={action.busy} onClick={() => act("replan-now")}>🔁 Replan & repost with AI</button>
          )}
          {job.status === "collecting" && (
            <a className="btn" href={`/jobs/${job.id}`} target="_blank" rel="noreferrer">View public page ↗</a>
          )}
          {!["filled", "cancelled"].includes(job.status) && (
            <button className="btn btn-danger" disabled={action.busy} onClick={() => confirm("Cancel this job?") && act("cancel")}>Cancel job</button>
          )}
          {["filled", "cancelled", "needs_attention"].includes(job.status) && (
            <button
              className="btn btn-danger"
              onClick={() => confirm("Delete this job and all its candidates permanently?") &&
                action.run(async () => { await api.del(`/api/hr/jobs/${id}`); navigate("/hr/jobs"); })}
            >
              Delete
            </button>
          )}
        </div>
      </div>

      <ErrorBox error={action.error} />
      {job.last_error && (
        <div className="alert bad row-between">
          <span>⚠️ Agent step failed: {job.last_error}</span>
          {job.can_retry && <button className="btn btn-sm" onClick={() => act("retry")}>Retry step</button>}
        </div>
      )}
      {working && !job.last_error && (
        <div className="alert info row"><Spinner /> The agent is working on this job ({job.status.replaceAll("_", " ")})...</div>
      )}

      {job.waiting_for === "jd_approval" && <JDApproval key={job.jd_version} job={job} onDone={reload} />}
      {job.waiting_for === "replan_approval" && <ReplanApproval key={job.replan_proposal?.created_at} job={job} onDone={reload} />}

      <div className="split">
        <div className="card">
          <div className="tabs">
            {[
              ["candidates", `Candidates (${job.applications.length})`],
              ["jd", "Job description"],
              ["postings", "Postings"],
              ["assessment", "Assessment"],
              ["request", "Request"],
            ].map(([k, l]) => (
              <button key={k} className={`tab${tab === k ? " active" : ""}`} onClick={() => setTab(k)}>{l}</button>
            ))}
          </div>
          {tab === "candidates" && <Candidates job={job} />}
          {tab === "jd" && (job.jd ? <><Markdown>{job.jd_markdown}</Markdown><Sources sources={job.jd_sources} /></> : <Empty title="JD not drafted yet" />)}
          {tab === "postings" && <Postings postings={job.postings} />}
          {tab === "assessment" && <AssessmentView a={job.assessment} />}
          {tab === "request" && (
            <div className="stack small">
              <div><b>Original HR request:</b> “{job.request_text}”</div>
              <div><b>Openings:</b> {job.openings} · <b>Shortlist size:</b> {job.shortlist_size} · <b>Min applications:</b> {job.min_applications}</div>
              <div><b>Application window:</b> {job.application_window_hours}h · <b>Deadline:</b> {fmtDate(job.deadline)}</div>
            </div>
          )}
        </div>
        <div className="card">
          <h2>Agent timeline</h2>
          <Timeline events={job.activity} />
        </div>
      </div>
    </>
  );
}

function Sources({ sources }) {
  if (!sources?.length) return null;
  return (
    <div className="alert info small" style={{ marginTop: 14 }}>
      <b>📚 Grounded in company knowledge (RAG):</b>{" "}
      {sources.map((s) => `[${s.ref}] ${s.title}`).join(" · ")}
    </div>
  );
}

// ---------------------------------------------------------------- JD approval (human-in-the-loop)
function JDApproval({ job, onDone }) {
  const [mode, setMode] = useState(null); // null | changes | edit
  const [feedback, setFeedback] = useState("");
  const [draft, setDraft] = useState(() => toForm(job.jd));
  const action = useAction();

  const send = (body) =>
    action.run(async () => {
      await api.post(`/api/hr/jobs/${job.id}/jd-decision`, body);
      await onDone();
    });

  return (
    <div className="card" style={{ borderColor: "var(--accent)", marginBottom: 16 }}>
      <div className="card-title">
        <h2>📝 Review the AI-drafted job description</h2>
        <span className="badge accent">Waiting for you</span>
      </div>
      {mode !== "edit" && (
        <div style={{ maxHeight: 420, overflow: "auto", border: "1px solid var(--border)", borderRadius: 10, padding: "4px 16px", marginBottom: 12 }}>
          <Markdown>{job.jd_markdown}</Markdown>
        </div>
      )}
      <Sources sources={job.jd_sources} />
      {job.jd?.policy_check && <p className="small muted">✅ <b>Policy check:</b> {job.jd.policy_check}</p>}

      {mode === "changes" && (
        <div className="field">
          <label htmlFor="fb">What should the agent change?</label>
          <textarea id="fb" value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="e.g. Add Kafka as must-have, make it remote-friendly, reduce responsibilities" />
          <div className="hint">Your feedback is also saved to the learning memory for future JDs.</div>
        </div>
      )}
      {mode === "edit" && <JDEditor value={draft} onChange={setDraft} />}
      <ErrorBox error={action.error} />
      <div className="row">
        {mode === null && (
          <>
            <button className="btn btn-ok" disabled={action.busy} onClick={() => send({ action: "approve" })}>✓ Approve & publish</button>
            <button className="btn" onClick={() => setMode("changes")}>✍️ Ask AI for changes</button>
            <button className="btn" onClick={() => setMode("edit")}>✏️ Edit myself</button>
            <span className="spacer" />
            <button className="btn btn-danger" disabled={action.busy} onClick={() => send({ action: "cancel" })}>Cancel job</button>
          </>
        )}
        {mode === "changes" && (
          <>
            <button className="btn btn-primary" disabled={action.busy || !feedback.trim()} onClick={() => send({ action: "changes", feedback })}>Send to agent</button>
            <button className="btn" onClick={() => setMode(null)}>Back</button>
          </>
        )}
        {mode === "edit" && (
          <>
            <button className="btn btn-ok" disabled={action.busy} onClick={() => send({ action: "approve", edited_jd: fromForm(draft, job.jd), feedback: "HR edited the JD manually" })}>✓ Save & publish</button>
            <button className="btn" onClick={() => setMode(null)}>Back</button>
          </>
        )}
      </div>
    </div>
  );
}

const LIST_FIELDS = ["responsibilities", "must_have_skills", "nice_to_have_skills", "qualifications", "benefits", "hiring_process"];
const TEXT_FIELDS = ["title", "location", "work_mode", "summary"];
const NUM_FIELDS = ["experience_min", "experience_max", "salary_min", "salary_max"];

function toForm(jd) {
  const f = { ...jd };
  LIST_FIELDS.forEach((k) => (f[k] = (jd?.[k] || []).join("\n")));
  return f;
}

function fromForm(f, original) {
  const out = { ...original, ...f };
  LIST_FIELDS.forEach((k) => (out[k] = String(f[k] || "").split("\n").map((s) => s.trim()).filter(Boolean)));
  NUM_FIELDS.forEach((k) => (out[k] = Number(f[k]) || 0));
  return out;
}

function JDEditor({ value, onChange }) {
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value });
  return (
    <div>
      <div className="grid grid-3">
        {TEXT_FIELDS.filter((k) => k !== "summary").map((k) => (
          <div className="field" key={k}><label>{k.replaceAll("_", " ")}</label><input value={value[k] || ""} onChange={set(k)} /></div>
        ))}
      </div>
      <div className="grid grid-4">
        {NUM_FIELDS.map((k) => (
          <div className="field" key={k}><label>{k.replaceAll("_", " ")}</label><input type="number" value={value[k] ?? ""} onChange={set(k)} /></div>
        ))}
      </div>
      <div className="field"><label>summary</label><textarea rows={3} value={value.summary || ""} onChange={set("summary")} /></div>
      <div className="grid grid-2">
        {LIST_FIELDS.map((k) => (
          <div className="field" key={k}><label>{k.replaceAll("_", " ")} (one per line)</label><textarea rows={5} value={value[k]} onChange={set(k)} /></div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Replan approval
function ReplanApproval({ job, onDone }) {
  const p = job.replan_proposal;
  const [feedback, setFeedback] = useState("");
  const action = useAction();
  const send = (act) =>
    action.run(async () => {
      await api.post(`/api/hr/jobs/${job.id}/replan-decision`, { action: act, feedback });
      await onDone();
    });
  if (!p) return null;
  const m = p.metrics || {};
  return (
    <div className="card" style={{ borderColor: "var(--accent)", marginBottom: 16 }}>
      <div className="card-title">
        <h2>🔁 The agent wants to replan this posting</h2>
        <span className="badge accent">Waiting for you</span>
      </div>
      <div className="grid grid-4" style={{ marginBottom: 12 }}>
        {[["Applications", `${m.applications}/${m.min_applications}`], ["Qualified", `${m.qualified}/${m.openings}`], ["Avg score", m.avg_score], ["Replans used", m.replans_used]].map(([l, v]) => (
          <div key={l} className="card stat" style={{ padding: 12 }}><div className="label">{l}</div><div className="value" style={{ fontSize: 22 }}>{v}</div></div>
        ))}
      </div>
      <p><b>Diagnosis:</b> {p.diagnosis}</p>
      <p>
        <b>Strategy:</b> <span className="badge info">{p.strategy}</span> · <b>{job.title}</b> → <b>{p.new_jd?.title}</b> · repost for{" "}
        {p.extend_hours}h
      </p>
      <ul className="small">{p.changes?.map((c, i) => <li key={i}>{c}</li>)}</ul>
      <details style={{ marginBottom: 12 }}>
        <summary className="small" style={{ cursor: "pointer" }}>Preview new JD</summary>
        <div className="small" style={{ marginTop: 8 }}>
          <p>{p.new_jd?.summary}</p>
          <p><b>Must have:</b> {p.new_jd?.must_have_skills?.join(", ")}</p>
          <p><b>Experience:</b> {p.new_jd?.experience_min}-{p.new_jd?.experience_max} yrs · <b>Salary:</b> {money(p.new_jd?.salary_min, p.new_jd?.currency)} - {money(p.new_jd?.salary_max, p.new_jd?.currency)} · <b>Mode:</b> {p.new_jd?.work_mode}</p>
        </div>
      </details>
      <div className="field">
        <label htmlFor="rfb">Feedback (needed for "Ask for another plan")</label>
        <input id="rfb" value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="e.g. Don't change the title, offer remote instead" />
      </div>
      <ErrorBox error={action.error} />
      <div className="row">
        <button className="btn btn-ok" disabled={action.busy} onClick={() => send("approve")}>✓ Approve & repost</button>
        <button className="btn" disabled={action.busy || !feedback.trim()} onClick={() => send("changes")}>Ask for another plan</button>
        <button className="btn" disabled={action.busy} onClick={() => send("reject")}>Skip, screen current applicants</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- tabs
function Candidates({ job }) {
  const navigate = useNavigate();
  if (!job.applications.length)
    return <Empty icon="📥" title="No applications yet">Share the job link. Applicants are screened automatically as they apply.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>#</th><th>Candidate</th><th>Stage</th><th>Resume</th><th>Test</th><th>Interview</th><th>AI verdict</th></tr>
        </thead>
        <tbody>
          {job.applications.map((a) => (
            <tr key={a.id} className="clickable" onClick={() => navigate(`/hr/candidates/${a.id}`)}>
              <td className="faint">{a.rank ?? "-"}</td>
              <td>
                <b>{a.name}</b>
                <div className="faint">{a.experience != null ? `${a.experience} yrs · ` : ""}{a.email}</div>
                {a.last_error && <span className="badge bad">error</span>}
              </td>
              <td><StageBadge stage={a.stage} /></td>
              <td style={{ minWidth: 110 }}>{a.score == null ? <span className="faint row"><Spinner /> scoring</span> : <ScoreBar value={a.score} />}</td>
              <td style={{ minWidth: 100 }}><ScoreBar value={a.assessment_score} /></td>
              <td style={{ minWidth: 100 }}><ScoreBar value={a.interview_score} /></td>
              <td><RecBadge rec={a.recommendation} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Postings({ postings }) {
  if (!postings || !Object.keys(postings).length) return <Empty icon="📢" title="Not posted yet">Approve the JD to publish it.</Empty>;
  const { careers, linkedin, naukri, telegram } = postings;
  const tone = (s) => (s === "live" ? "ok" : s === "ready" ? "info" : s === "failed" ? "bad" : "");
  return (
    <div className="stack">
      <div className="row-between">
        <div><b>🌐 Careers site</b> <span className={`badge ${tone(careers?.status)}`}>{careers?.status}</span></div>
        {careers?.url && <div className="row"><a href={careers.url} target="_blank" rel="noreferrer" className="small">{careers.url}</a><CopyButton text={careers.url} /></div>}
      </div>
      <div>
        <div className="row-between" style={{ marginBottom: 6 }}>
          <div><b>💼 LinkedIn</b> <span className={`badge ${tone(linkedin?.status)}`}>{linkedin?.status}</span></div>
          <div className="row">
            <CopyButton text={linkedin?.text || ""} label="Copy post" />
            <a className="btn btn-sm btn-primary" href={linkedin?.share_url} target="_blank" rel="noreferrer">Share on LinkedIn ↗</a>
          </div>
        </div>
        <div className="copybox">{linkedin?.text}</div>
        <div className="faint">{linkedin?.note}</div>
      </div>
      <div>
        <div className="row-between" style={{ marginBottom: 6 }}>
          <div><b>🟦 Naukri</b> <span className={`badge ${tone(naukri?.status)}`}>{naukri?.status}</span></div>
          <div className="row">
            <CopyButton text={naukri?.text || ""} label="Copy listing" />
            <a className="btn btn-sm" href="https://recruit.naukri.com/" target="_blank" rel="noreferrer">Open Naukri recruiter ↗</a>
          </div>
        </div>
        <div className="copybox">{naukri?.text}</div>
        <div className="faint">{naukri?.note}</div>
      </div>
      <div><b>✈️ Telegram channel</b> <span className={`badge ${tone(telegram?.status)}`}>{telegram?.status?.replaceAll("_", " ")}</span>
        {telegram?.status === "not_configured" && <span className="faint"> · add TELEGRAM_CHANNEL_ID to auto-post</span>}
      </div>
    </div>
  );
}

function AssessmentView({ a }) {
  if (!a) return <Empty icon="📝" title="Assessment not generated yet">It is created automatically when the JD is published.</Empty>;
  return (
    <div className="stack">
      <p className="muted small">{a.duration_minutes} minutes · {a.mcqs?.length} MCQs · {a.coding?.length} coding problem(s). Answers are visible only to HR.</p>
      {a.mcqs?.map((m, i) => (
        <div key={i} className="small">
          <b>Q{i + 1}. {m.question}</b> <span className="chip">{m.skill}</span>
          <ol type="A" style={{ margin: "4px 0" }}>
            {m.options.map((o, j) => <li key={j} style={j === m.answer_index ? { color: "var(--ok)", fontWeight: 600 } : undefined}>{o}{j === m.answer_index ? " ✓" : ""}</li>)}
          </ol>
        </div>
      ))}
      {a.coding?.map((p, i) => (
        <div key={i}>
          <h3>💻 {p.title} <span className="chip">{p.difficulty}</span></h3>
          <p className="small" style={{ whiteSpace: "pre-wrap" }}>{p.description}</p>
          <details><summary className="small" style={{ cursor: "pointer" }}>Reference solution & {p.tests.length} verified tests</summary>
            <pre className="copybox">{p.reference_solution}</pre>
            {p.tests.map((t, j) => <div key={j} className="test-row"><span>{p.function_name}(*{t.args_json})</span><span>→ {t.expected_json}</span></div>)}
          </details>
        </div>
      ))}
    </div>
  );
}
