import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api.js";
import { Empty, ErrorBox, JobStatus, Loading, fmtDate, timeLeft, useData } from "../../components/ui.jsx";
import NewJobModal from "./NewJobModal.jsx";

export default function Jobs() {
  const [showNew, setShowNew] = useState(false);
  const navigate = useNavigate();
  const { data: jobs, error, loading } = useData(() => api.get("/api/hr/jobs"), [], 8000);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Jobs</h1>
          <p>Every job is a LangGraph workflow: draft → approve → post → replan → screen → hire.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowNew(true)}>✨ New hiring request</button>
      </div>
      <ErrorBox error={error} />
      <div className="card">
        {loading && !jobs ? (
          <Loading />
        ) : !jobs?.length ? (
          <Empty icon="💼" title="No jobs yet">Create your first hiring request.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Role</th><th>Status</th><th>Applicants</th><th>In pipeline</th><th>Hired</th><th>Replans</th><th>Deadline</th><th>Created</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id} className="clickable" onClick={() => navigate(`/hr/jobs/${j.id}`)}>
                    <td>
                      <b>{j.title}</b>
                      <div className="faint">{[j.department, j.location, j.work_mode].filter(Boolean).join(" · ")}</div>
                    </td>
                    <td><JobStatus status={j.status} />{j.last_error && <span className="badge bad" style={{ marginLeft: 6 }}>error</span>}</td>
                    <td>{j.counts.applications}</td>
                    <td>{j.counts.in_pipeline}</td>
                    <td>{j.counts.hired}/{j.openings}</td>
                    <td>{j.replan_count}</td>
                    <td className="small">{j.status === "collecting" ? timeLeft(j.deadline) : "-"}</td>
                    <td className="faint">{fmtDate(j.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {showNew && <NewJobModal onClose={() => setShowNew(false)} />}
    </>
  );
}
