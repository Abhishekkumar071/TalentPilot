import { api } from "../../api.js";
import { ErrorBox, Loading, useData } from "../../components/ui.jsx";

const ROWS = [
  ["llm_groq", "Groq LLM (primary)", "GROQ_API_KEY", "https://console.groq.com/keys"],
  ["llm_gemini", "Gemini LLM (fallback) + embeddings", "GEMINI_API_KEY", "https://aistudio.google.com/apikey"],
  ["telegram_hr", "Telegram HR alerts", "TELEGRAM_BOT_TOKEN + TELEGRAM_HR_CHAT_ID", "https://t.me/BotFather"],
  ["telegram_channel", "Telegram job channel posting", "TELEGRAM_CHANNEL_ID", "https://t.me/BotFather"],
  ["sms", "SMS (optional)", "TWILIO_ACCOUNT_SID / TOKEN / FROM", "https://www.twilio.com/try-twilio"],
];

export default function Settings() {
  const { data, error, loading } = useData(() => api.get("/api/hr/integrations"), []);
  if (loading) return <Loading />;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings & integrations</h1>
          <p>All services run on free tiers. Keys are set as environment variables on the server (backend/.env locally, Render dashboard in production).</p>
        </div>
      </div>
      <ErrorBox error={error} />
      {data && (
        <div className="card">
          <table>
            <thead><tr><th>Integration</th><th>Status</th><th>Env variable</th><th>Get a free key</th></tr></thead>
            <tbody>
              {ROWS.map(([k, label, env, url]) => (
                <tr key={k}>
                  <td><b>{label}</b></td>
                  <td>{data[k] ? <span className="badge ok">Connected</span> : <span className="badge">Not set</span>}</td>
                  <td className="mono small">{env}</td>
                  <td><a href={url} target="_blank" rel="noreferrer" className="small">{url.replace("https://", "")} ↗</a></td>
                </tr>
              ))}
              <tr><td><b>Email</b></td><td><span className={`badge ${data.email === "console" ? "warn" : "ok"}`}>{data.email}</span></td><td className="mono small">EMAIL_PROVIDER=brevo + BREVO_API_KEY</td><td><a href="https://www.brevo.com" target="_blank" rel="noreferrer" className="small">brevo.com ↗</a></td></tr>
              <tr><td><b>Database</b></td><td><span className={`badge ${data.database === "postgres" ? "ok" : "warn"}`}>{data.database}</span></td><td className="mono small">DATABASE_URL</td><td><a href="https://neon.tech" target="_blank" rel="noreferrer" className="small">neon.tech ↗</a></td></tr>
              <tr><td><b>Semantic search</b></td><td>{data.embeddings ? <span className="badge ok">Gemini embeddings</span> : <span className="badge">BM25 keyword only</span>}</td><td className="mono small">GEMINI_API_KEY</td><td /></tr>
            </tbody>
          </table>
          <p className="faint" style={{ marginTop: 12 }}>Public URL used in emails: <b>{data.public_url}</b> · Company: <b>{data.company}</b> · LLM provider order: <b>{data.llm_provider}</b></p>
          {data.email === "console" && <div className="alert warn small">Emails are only printed in the server log. Set EMAIL_PROVIDER=brevo so candidates actually receive invites.</div>}
        </div>
      )}
    </>
  );
}
