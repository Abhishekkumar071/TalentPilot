import { Link } from "react-router-dom";
import { api } from "../../api.js";
import { useConfig } from "../../components/Layout.jsx";
import { Empty, ErrorBox, Loading, timeLeft, useData } from "../../components/ui.jsx";

export default function Careers() {
  const cfg = useConfig();
  const { data: jobs, error, loading } = useData(() => api.get("/api/public/jobs"), [], 30000);

  return (
    <>
      <section className="hero">
        <h1>Build what's next with {cfg.company || "us"}</h1>
        <p className="muted" style={{ fontSize: 16, maxWidth: 640 }}>
          Explore open roles. Every application is reviewed by our AI recruiter and a human hiring team. Fast, fair and
          transparent.
        </p>
      </section>
      <ErrorBox error={error} />
      {loading && !jobs ? (
        <Loading />
      ) : !jobs?.length ? (
        <div className="card">
          <Empty icon="🌱" title="No open roles right now">
            Check back soon. New roles are posted regularly.
          </Empty>
        </div>
      ) : (
        <div className="stack">
          {jobs.map((j) => (
            <Link key={j.id} to={`/jobs/${j.id}`} className="card job-card">
              <div className="row-between">
                <div>
                  <h2 style={{ marginBottom: 4 }}>{j.title}</h2>
                  <div className="muted small">
                    {j.department} · 📍 {j.location} · {j.work_mode} · {j.employment_type} · {j.experience}
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  {j.salary && <div className="small" style={{ fontWeight: 600 }}>{j.salary}</div>}
                  <div className="faint">{timeLeft(j.deadline)}</div>
                </div>
              </div>
              <p className="muted small" style={{ margin: "10px 0" }}>{j.summary}</p>
              <div className="chips">
                {j.skills.slice(0, 8).map((s) => (
                  <span key={s} className="chip">{s}</span>
                ))}
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
