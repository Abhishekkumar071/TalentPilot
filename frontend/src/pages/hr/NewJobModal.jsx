import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api.js";
import { ErrorBox, Modal, useAction } from "../../components/ui.jsx";

const EXAMPLES = [
  "Hire a backend engineer with Python and PostgreSQL, 3-5 years, Noida hybrid",
  "We need 2 React frontend developers, 1-3 years experience",
  "Hire a DevOps engineer who knows Kubernetes and AWS",
];

const WINDOWS = [
  [0.05, "3 minutes (demo)"],
  [1, "1 hour"],
  [24, "1 day"],
  [72, "3 days"],
  [168, "7 days"],
  [336, "14 days"],
];

export default function NewJobModal({ onClose }) {
  const [f, setF] = useState({
    request_text: "",
    location: "",
    work_mode: "",
    openings: 1,
    application_window_hours: 72,
    min_applications: 5,
    shortlist_size: 3,
  });
  const action = useAction();
  const navigate = useNavigate();
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "number" || k === "application_window_hours" ? Number(e.target.value) : e.target.value });

  const submit = (e) => {
    e.preventDefault();
    action.run(async () => {
      const { id } = await api.post("/api/hr/jobs", f);
      navigate(`/hr/jobs/${id}`);
    });
  };

  return (
    <Modal title="🤖 New hiring request" onClose={onClose}>
      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="req">Tell the agent who you want to hire</label>
          <textarea
            id="req"
            required
            minLength={5}
            rows={3}
            placeholder="e.g. Hire a backend engineer"
            value={f.request_text}
            onChange={set("request_text")}
          />
          <div className="chips" style={{ marginTop: 8 }}>
            {EXAMPLES.map((x) => (
              <button type="button" key={x} className="chip" style={{ cursor: "pointer" }} onClick={() => setF({ ...f, request_text: x })}>
                {x}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-3">
          <div className="field">
            <label htmlFor="loc">Location (optional)</label>
            <input id="loc" placeholder="Agent decides" value={f.location} onChange={set("location")} />
          </div>
          <div className="field">
            <label htmlFor="mode">Work mode</label>
            <select id="mode" value={f.work_mode} onChange={set("work_mode")}>
              <option value="">Agent decides</option>
              <option value="onsite">Onsite</option>
              <option value="hybrid">Hybrid</option>
              <option value="remote">Remote</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="open">Openings</label>
            <input id="open" type="number" min={1} max={50} value={f.openings} onChange={set("openings")} />
          </div>
        </div>
        <div className="grid grid-3">
          <div className="field">
            <label htmlFor="win">Applications open for</label>
            <select id="win" value={f.application_window_hours} onChange={set("application_window_hours")}>
              {WINDOWS.map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="min">Min. applications</label>
            <input id="min" type="number" min={1} value={f.min_applications} onChange={set("min_applications")} />
            <div className="hint">Fewer → AI replans the JD</div>
          </div>
          <div className="field">
            <label htmlFor="short">Shortlist size</label>
            <input id="short" type="number" min={1} max={50} value={f.shortlist_size} onChange={set("shortlist_size")} />
          </div>
        </div>
        <div className="alert info small">
          The agent will retrieve your company policies, salary bands and benefits (RAG), draft the JD and wait for your
          approval before posting anything.
        </div>
        <ErrorBox error={action.error} />
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={action.busy}>
            {action.busy ? "Starting agent..." : "Start hiring"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
