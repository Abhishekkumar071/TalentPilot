import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, auth } from "../../api.js";
import { ErrorBox, useAction } from "../../components/ui.jsx";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const action = useAction();
  const navigate = useNavigate();

  const submit = (e) => {
    e.preventDefault();
    action.run(async () => {
      const res = await api.post("/api/auth/login", { email, password });
      auth.set(res.token);
      navigate("/hr");
    });
  };

  return (
    <div className="center" style={{ minHeight: "100vh", padding: 16 }}>
      <form className="card" style={{ width: "min(400px, 100%)" }} onSubmit={submit}>
        <div className="brand" style={{ padding: "0 0 14px" }}>
          <span className="brand-logo">🧭</span>TalentPilot
        </div>
        <h2>Recruiter sign in</h2>
        <p className="muted small">Agentic AI hiring: from job request to signed offer.</p>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <ErrorBox error={action.error} />
        <button className="btn btn-primary btn-lg" style={{ width: "100%" }} disabled={action.busy}>
          {action.busy ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
