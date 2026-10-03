import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api.js";
import { ErrorBox, Loading, money, useAction, useData } from "../../components/ui.jsx";

export default function Offer() {
  const { token } = useParams();
  const { data, error, loading, reload } = useData(() => api.get(`/api/candidate/${token}/offer`), [token]);
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const action = useAction();

  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data.available)
    return (
      <div className="card" style={{ textAlign: "center" }}>
        <h1>No offer available</h1>
        <p className="muted">This link doesn't have an active offer.</p>
      </div>
    );

  const o = data.offer;
  const respond = (act) =>
    action.run(async () => {
      await api.post(`/api/candidate/${token}/offer/respond`, { action: act, reason });
      setTimeout(reload, 800);
    });

  const accepted = data.stage === "hired" || data.response?.action === "accept";
  const declined = data.stage === "offer_declined" || data.response?.action === "decline";

  return (
    <div className="card" style={{ maxWidth: 720, margin: "0 auto" }}>
      <p className="faint">{data.company}</p>
      <h1>{accepted ? "🎉 Welcome aboard!" : declined ? "Offer declined" : `Congratulations, ${data.name.split(" ")[0]}! 🎉`}</h1>
      <p className="muted">
        {accepted
          ? "You've accepted the offer. Our onboarding team will reach out with next steps."
          : declined
            ? "Thank you for letting us know. We wish you the very best."
            : `We're delighted to offer you the position of ${o.position}.`}
      </p>
      <div className="grid grid-2" style={{ margin: "16px 0" }}>
        <div className="card stat"><div className="label">Position</div><b>{o.position}</b></div>
        <div className="card stat"><div className="label">Annual CTC</div><b>{money(o.annual_ctc, o.currency)}</b></div>
        <div className="card stat"><div className="label">Joining date</div><b>{o.joining_date}</b></div>
        <div className="card stat"><div className="label">Location</div><b>{o.location}</b></div>
      </div>
      <p style={{ whiteSpace: "pre-wrap" }}>{o.letter_body}</p>
      {o.benefits?.length > 0 && (
        <>
          <h3>Benefits</h3>
          <ul className="small">{o.benefits.map((b, i) => <li key={i}>{b}</li>)}</ul>
        </>
      )}
      <p className="small muted">Probation: {o.probation} · Offer valid until {o.valid_until}</p>
      <a className="btn" href={`/api/candidate/${token}/offer.pdf`} target="_blank" rel="noreferrer">📄 Download offer letter (PDF)</a>
      <ErrorBox error={action.error} />
      {data.stage === "offer_sent" && !data.response && (
        <div style={{ marginTop: 20 }}>
          {declining ? (
            <div className="stack">
              <label htmlFor="reason">Could you share why? (optional, helps us improve)</label>
              <textarea id="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
              <div className="row">
                <button className="btn btn-danger" disabled={action.busy} onClick={() => respond("decline")}>Confirm decline</button>
                <button className="btn" onClick={() => setDeclining(false)}>Back</button>
              </div>
            </div>
          ) : (
            <div className="row">
              <button className="btn btn-ok btn-lg" disabled={action.busy} onClick={() => respond("accept")}>✓ Accept offer</button>
              <button className="btn btn-lg" disabled={action.busy} onClick={() => setDeclining(true)}>Decline</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
