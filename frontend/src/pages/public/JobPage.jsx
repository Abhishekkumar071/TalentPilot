import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api.js";
import { ErrorBox, Loading, Markdown, timeLeft, useAction, useData } from "../../components/ui.jsx";

export default function JobPage() {
  const { id } = useParams();
  const { data: job, error, loading } = useData(() => api.get(`/api/public/jobs/${id}`), [id]);
  const [form, setForm] = useState({ name: "", email: "", phone: "", consent: false });
  const [file, setFile] = useState(null);
  const [done, setDone] = useState(null);
  const action = useAction();

  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;

  const submit = (e) => {
    e.preventDefault();
    action.run(async () => {
      const fd = new FormData();
      Object.entries(form).forEach(([k, v]) => fd.append(k, String(v)));
      fd.append("resume", file);
      const res = await api.upload(`/api/public/jobs/${id}/apply`, fd);
      setDone(res.message);
    });
  };

  return (
    <div className="split">
      <div className="card">
        <Link to="/" className="small">← All jobs</Link>
        <div style={{ marginTop: 12 }}>
          <Markdown>{job.jd_markdown}</Markdown>
        </div>
      </div>
      <aside className="card" style={{ position: "sticky", top: 16 }}>
        {done ? (
          <div className="stack" style={{ textAlign: "center" }}>
            <div style={{ fontSize: 44 }}>🎉</div>
            <h2>Application received</h2>
            <p className="muted">{done}</p>
            <p className="faint">Check your inbox (and spam folder) for the next steps.</p>
          </div>
        ) : !job.open ? (
          <div className="alert warn">Applications for this role are closed.</div>
        ) : (
          <form onSubmit={submit}>
            <h2>Apply for this role</h2>
            <p className="faint">{timeLeft(job.deadline)} to apply</p>
            <div className="field">
              <label htmlFor="name">Full name</label>
              <input id="name" required minLength={2} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="email">Email</label>
              <input id="email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="phone">Phone (optional, for SMS updates)</label>
              <input id="phone" type="tel" placeholder="+91..." value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="resume">Resume (PDF, DOCX or TXT, max 5 MB)</label>
              <input id="resume" type="file" required accept=".pdf,.docx,.txt" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            </div>
            <label className="checkbox field">
              <input type="checkbox" required checked={form.consent} onChange={(e) => setForm({ ...form, consent: e.target.checked })} />
              <span className="small muted">
                I agree that my resume is analysed by AI (identity details are hidden from scoring) and that later rounds use
                camera-based proctoring. Final decisions are always made by a human recruiter.
              </span>
            </label>
            <ErrorBox error={action.error} />
            <button className="btn btn-primary btn-lg" style={{ width: "100%" }} disabled={action.busy || !file}>
              {action.busy ? "Submitting..." : "Submit application"}
            </button>
          </form>
        )}
      </aside>
    </div>
  );
}
