import { createContext, useContext, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api, auth } from "../api.js";

const ConfigContext = createContext({ app_name: "TalentPilot", company: "" });
export const useConfig = () => useContext(ConfigContext);

function useBranding() {
  const [cfg, setCfg] = useState({ app_name: "TalentPilot", company: "" });
  useEffect(() => {
    api.get("/api/public/config").then(setCfg).catch(() => {});
  }, []);
  return cfg;
}

const NAV = [
  ["/hr", "🏠", "Dashboard", true],
  ["/hr/jobs", "💼", "Jobs"],
  ["/hr/knowledge", "📚", "Knowledge base"],
  ["/hr/learning", "🧠", "Learning memory"],
  ["/hr/settings", "⚙️", "Settings"],
];

export function HRLayout() {
  const cfg = useBranding();
  const navigate = useNavigate();
  return (
    <ConfigContext.Provider value={cfg}>
      <div className="hr-shell">
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-logo">🧭</span>
            {cfg.app_name}
          </div>
          {NAV.map(([to, icon, label, end]) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
              <span>{icon}</span>
              {label}
            </NavLink>
          ))}
          <a className="nav-link" href="/" target="_blank" rel="noreferrer">
            <span>🌐</span>Careers site ↗
          </a>
          <button
            className="nav-link"
            style={{ border: "none", background: "none", font: "inherit", cursor: "pointer", textAlign: "left" }}
            onClick={() => {
              auth.clear();
              navigate("/hr/login");
            }}
          >
            <span>🚪</span>Log out
          </button>
          <div className="sidebar-foot">
            Agentic recruitment for {cfg.company}
            <br />
            LangGraph · RAG · Groq
          </div>
        </aside>
        <main className="main">
          <Outlet />
        </main>
      </div>
    </ConfigContext.Provider>
  );
}

export function PublicLayout() {
  const cfg = useBranding();
  return (
    <ConfigContext.Provider value={cfg}>
      <header className="public-header">
        <Link to="/" className="brand" style={{ padding: 0, color: "inherit", textDecoration: "none" }}>
          <span className="brand-logo">🧭</span>
          {cfg.company || "Careers"}
          <span className="faint" style={{ fontWeight: 500 }}>
            Careers
          </span>
        </Link>
        <Link to="/hr" className="faint">
          Recruiter login
        </Link>
      </header>
      <main className="public-main">
        <Outlet />
      </main>
    </ConfigContext.Provider>
  );
}
