import { useState } from "react";
import { api } from "../../api.js";
import { Empty, ErrorBox, Loading, fmtDate, useAction, useData } from "../../components/ui.jsx";

const KIND = {
  jd_changes: "JD change request", jd_manual_edit: "JD edited by HR", replan_changes: "Replan feedback",
  replan_reject: "Replan rejected", candidate_rejected: "Candidate rejected", candidate_override: "Overrode AI verdict",
  offer_edited: "Offer edited", offer_redraft: "Offer redraft", hr_guideline: "HR guideline",
};

export default function Learning() {
  const { data, error, loading, reload } = useData(() => api.get("/api/hr/feedback"), []);
  const [note, setNote] = useState("");
  const [role, setRole] = useState("");
  const action = useAction();

  return (
    <>
      <div className="page-head">
        <div>
          <h1>🧠 Learning memory</h1>
          <p>Every HR correction is remembered and fed back into the agents' prompts, so JDs, screening and offers improve over time without retraining a model.</p>
        </div>
      </div>
      <ErrorBox error={error || action.error} />
      <div className="card">
        <h2>Teach the agents a rule</h2>
        <form className="stack" onSubmit={(e) => { e.preventDefault(); action.run(async () => { await api.post("/api/hr/feedback", { note, role }); setNote(""); await reload(); }); }}>
          <div className="grid grid-2">
            <div className="field" style={{ marginBottom: 0 }}><label>Guideline</label><input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Always value open-source contributions for engineering roles" /></div>
            <div className="field" style={{ marginBottom: 0 }}><label>Applies to role (optional)</label><input value={role} onChange={(e) => setRole(e.target.value)} placeholder="e.g. Backend Engineer" /></div>
          </div>
          <div><button className="btn btn-primary" disabled={note.trim().length < 3 || action.busy}>Save guideline</button></div>
        </form>
      </div>
      <div className="card">
        {loading && !data ? <Loading /> : !data?.length ? (
          <Empty icon="🧠" title="Nothing learned yet">Approve, edit or reject things in the workflow and the lessons will appear here.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Type</th><th>Lesson</th><th>Role</th><th>When</th><th /></tr></thead>
              <tbody>
                {data.map((f) => (
                  <tr key={f.id}>
                    <td><span className="badge info">{KIND[f.kind] || f.kind}</span></td>
                    <td className="small">{f.note}</td>
                    <td className="small">{f.role || <span className="faint">all roles</span>}</td>
                    <td className="faint">{fmtDate(f.created_at)}</td>
                    <td><button className="btn btn-sm btn-danger" onClick={() => action.run(async () => { await api.del(`/api/hr/feedback/${f.id}`); await reload(); })}>Forget</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
