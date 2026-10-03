import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api.js";
import { Empty, ErrorBox, JobStatus, Loading, Timeline, timeLeft, useData } from "../../components/ui.jsx";
import NewJobModal from "./NewJobModal.jsx";

const ACTION_LABEL = {
  awaiting_jd_approval: ["📝", "Approve job description"],
  awaiting_replan_approval: ["🔁", "Approve replanned posting"],
  needs_attention: ["⚠️", "Job needs attention"],
  hr_review: ["🧑‍⚖️", "Decide on candidate"],
  offer_review: ["📨", "Approve offer letter"],
  error: ["🛠️", "Agent error, retry"],
};

const FUNNEL = [
  ["Applied", ["applied", "screened"]],
  ["Shortlisted", ["shortlisted", "waitlisted"]],
  ["Assessment", ["assessment_sent", "assessment_done"]],
  ["Interview", ["interview_sent", "interview_done"]],
  ["Decision / Offer", ["hr_review", "offer_review", "offer_sent"]],
  ["Hired", ["hired"]],
];

export default function Dashboard() {
  const [showNew, setShowNew] = useState(false);
  const navigate = useNavigate();
  const { data, error, loading } = useData(() => api.get("/api/hr/overview"), [], 8000);

  if (loading && !data) return <Loading />;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>Your AI recruiting agents are working. You approve the decisions that matter.</p>
        </div>
        <button className="btn btn-primary btn-lg" onClick={() => setShowNew(true)}>
          ✨ New hiring request
        </button>
      </div>
      <ErrorBox error={error} />
      {data && (
        <>
          <div className="grid grid-4">
            {[
              ["Open jobs", data.stats.open_jobs],
              ["Applications", data.stats.applications],
              ["In pipeline", data.stats.in_pipeline],
              ["Hired", data.stats.hired],
            ].map(([l, v]) => (
              <div key={l} className="card stat">
                <div className="label">{l}</div>
                <div className="value">{v}</div>
              </div>
            ))}
          </div>

          <div className="split" style={{ marginTop: 16 }}>
            <div className="stack">
              <div className="card">
                <div className="card-title">
                  <h2>👉 Needs your action</h2>
                  <span className="badge accent">{data.action_items.length}</span>
                </div>
                {data.action_items.length === 0 ? (
                  <p className="muted small">Nothing waiting on you. The agents are handling everything.</p>
                ) : (
                  <div className="stack" style={{ gap: 8 }}>
                    {data.action_items.map((a, i) => {
                      const [ico, label] = ACTION_LABEL[a.kind] || ["•", a.kind];
                      return (
                        <Link key={i} to={a.link} className="alert action row-between" style={{ marginBottom: 0 }}>
                          <span>
                            {ico} <b>{label}</b> · {a.title}
                          </span>
                          <span>→</span>
                        </Link>
                      );
                    })}
                  </div>
                )}
              </div>

              <div className="card">
                <div className="card-title">
                  <h2>Jobs</h2>
                  <Link to="/hr/jobs" className="small">View all</Link>
                </div>
                {data.jobs.length === 0 ? (
                  <Empty icon="💼" title="No jobs yet">
                    Click <b>New hiring request</b> and type something like "Hire a backend engineer".
                  </Empty>
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr><th>Role</th><th>Status</th><th>Applicants</th><th>Deadline</th></tr>
                      </thead>
                      <tbody>
                        {data.jobs.map((j) => (
                          <tr key={j.id} className="clickable" onClick={() => navigate(`/hr/jobs/${j.id}`)}>
                            <td><b>{j.title}</b><div className="faint">{j.location}</div></td>
                            <td><JobStatus status={j.status} /></td>
                            <td>{j.counts.applications}</td>
                            <td className="small">{j.status === "collecting" ? timeLeft(j.deadline) : "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div className="card">
                <h2>Hiring funnel</h2>
                <div className="stack" style={{ gap: 8, marginTop: 8 }}>
                  {FUNNEL.map(([label, stages]) => {
                    const n = stages.reduce((s, k) => s + (data.funnel[k] || 0), 0);
                    const max = Math.max(1, data.stats.applications);
                    return (
                      <div key={label} className="row" style={{ flexWrap: "nowrap" }}>
                        <span className="small" style={{ width: 130 }}>{label}</span>
                        <div className="bar" style={{ height: 14 }}>
                          <span style={{ width: `${(n / max) * 100}%` }} />
                        </div>
                        <b className="small" style={{ width: 28, textAlign: "right" }}>{n}</b>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            <div className="card">
              <h2>Live agent activity</h2>
              <Timeline events={data.recent_activity} />
            </div>
          </div>
        </>
      )}
      {showNew && <NewJobModal onClose={() => setShowNew(false)} />}
    </>
  );
}
