import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import Editor from "@monaco-editor/react";
import { api } from "../../api.js";
import { ErrorBox, Loading, useAction, useData } from "../../components/ui.jsx";
import CameraPanel from "../../proctor/CameraPanel.jsx";
import { runTests, warmUpPython } from "../../proctor/pyRunner.js";
import { enterFullscreen, useProctor } from "../../proctor/useProctor.js";

const store = {
  load(key) { try { return JSON.parse(localStorage.getItem(key)) || null; } catch { return null; } },
  save(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* ignore */ } },
  clear(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
};

export default function Assessment() {
  const { token } = useParams();
  const { data, error, loading } = useData(() => api.get(`/api/candidate/${token}/assessment`), [token]);
  const [phase, setPhase] = useState("intro"); // intro | running | submitted
  const proctor = useProctor({ token, phase: "assessment", detectVoice: true });

  if (loading) return <Loading text="Loading your assessment..." />;
  if (error) return <Centered><ErrorBox error={error} /></Centered>;
  if (phase === "submitted")
    return (
      <Centered>
        <div style={{ fontSize: 48 }}>✅</div>
        <h1>Assessment submitted</h1>
        <p className="muted">Thank you, {data.name}! Our AI is grading your answers. You'll receive an email about the next step soon.</p>
      </Centered>
    );
  if (!data.available)
    return (
      <Centered>
        <h1>Assessment not available</h1>
        <p className="muted">This assessment has already been submitted or the link has expired.</p>
      </Centered>
    );
  if (phase === "intro") return <Intro data={data} proctor={proctor} onStart={() => setPhase("running")} token={token} />;
  return <Exam data={data} token={token} proctor={proctor} onSubmitted={() => setPhase("submitted")} />;
}

function Centered({ children }) {
  return (
    <div className="center" style={{ minHeight: "100vh", padding: 16, textAlign: "center" }}>
      <div className="card" style={{ maxWidth: 560 }}>{children}</div>
    </div>
  );
}

function Intro({ data, proctor, onStart, token }) {
  const [agree, setAgree] = useState(false);
  const action = useAction();
  const a = data.assessment;
  useEffect(() => { if (a.coding.length) warmUpPython(); }, [a.coding.length]);

  const begin = () =>
    action.run(async () => {
      const ok = await proctor.start();
      if (!ok) return;
      await enterFullscreen();
      await api.post(`/api/candidate/${token}/assessment/start`);
      onStart();
    });

  return (
    <div className="public-main" style={{ maxWidth: 760 }}>
      <div className="card">
        <p className="faint">{data.company}</p>
        <h1>Online assessment: {data.job_title}</h1>
        <p className="muted">Hi {data.name}, this test has <b>{a.mcqs.length} multiple-choice questions</b>
          {a.coding.length > 0 && <> and <b>{a.coding.length} Python coding problem</b></>}. You have <b>{a.duration_minutes} minutes</b>
          {data.started_at && " (your timer already started earlier)"}.</p>
        <h3>AI proctoring rules</h3>
        <ul className="small">
          <li>Your camera and microphone stay on. AI checks that you are alone, facing the screen, and not using a phone.</li>
          <li>Stay in full-screen. Switching tabs, leaving the window and pasting large text are recorded.</li>
          <li>Flags are reviewed by a human recruiter. Nothing is rejected automatically.</li>
          <li>The test auto-submits when time runs out. Use Chrome or Edge on a laptop for best results.</li>
        </ul>
        <label className="checkbox field">
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
          <span className="small">I consent to camera, microphone and screen-activity monitoring for this assessment.</span>
        </label>
        <ErrorBox error={action.error || proctor.error} />
        <button className="btn btn-primary btn-lg" disabled={!agree || action.busy} onClick={begin}>
          {action.busy ? "Starting camera..." : "Start assessment"}
        </button>
      </div>
    </div>
  );
}

function Exam({ data, token, proctor, onSubmitted }) {
  const a = data.assessment;
  const key = `tp_assess_${token}`;
  const saved = useMemo(() => store.load(key), [key]);
  const [answers, setAnswers] = useState(saved?.answers || {});
  const [code, setCode] = useState(saved?.code || Object.fromEntries(a.coding.map((p, i) => [i, p.starter_code])));
  const [results, setResults] = useState({});
  const [current, setCurrent] = useState(0);
  const [deadline, setDeadline] = useState(null);
  const [left, setLeft] = useState(null);
  const [fullscreen, setFullscreen] = useState(!!document.fullscreenElement);
  const submitting = useRef(false);
  const action = useAction();
  const total = a.mcqs.length + a.coding.length;

  useEffect(() => { store.save(key, { answers, code }); }, [key, answers, code]);

  // Server-authoritative timer
  useEffect(() => {
    api.post(`/api/candidate/${token}/assessment/start`).then((r) => {
      const skew = Date.now() - new Date(r.server_time).getTime();
      setDeadline(new Date(r.started_at).getTime() + skew + a.duration_minutes * 60000);
    });
  }, [token, a.duration_minutes]);

  useEffect(() => {
    const onFs = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  const run = useCallback(async (i) => {
    const p = a.coding[i];
    setResults((r) => ({ ...r, [i]: { running: true } }));
    const out = await runTests({ code: code[i], fn: p.function_name, tests: p.tests });
    const passed = out.results ? out.results.filter((t) => t.pass).length : 0;
    const res = { ...out, passed, total: p.tests.length };
    setResults((r) => ({ ...r, [i]: res }));
    return res;
  }, [a.coding, code]);

  const submit = useCallback(async (auto = false) => {
    if (submitting.current) return;
    submitting.current = true;
    await action.run(async () => {
      const coding = {};
      for (let i = 0; i < a.coding.length; i++) {
        const r = results[i]?.results ? results[i] : await run(i);
        coding[i] = { code: code[i], passed: r.passed || 0, total: a.coding[i].tests.length };
      }
      await api.post(`/api/candidate/${token}/assessment/submit`, { mcq_answers: answers, coding, auto_submitted: auto });
      await proctor.stop();
      store.clear(key);
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      onSubmitted();
    });
    submitting.current = false;
  }, [a.coding, action, answers, code, key, onSubmitted, proctor, results, run, token]);

  useEffect(() => {
    if (!deadline) return undefined;
    const t = setInterval(() => {
      const s = Math.max(0, Math.round((deadline - Date.now()) / 1000));
      setLeft(s);
      if (s === 0) submit(true);
    }, 1000);
    return () => clearInterval(t);
  }, [deadline, submit]);

  const answered = (i) => (i < a.mcqs.length ? answers[i] !== undefined : (results[i - a.mcqs.length]?.passed ?? 0) > 0 || code[i - a.mcqs.length] !== a.coding[i - a.mcqs.length].starter_code);
  const mm = left == null ? "--:--" : `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(left % 60).padStart(2, "0")}`;

  return (
    <div className="exam-shell">
      <div className="exam-top">
        <b>{data.job_title}</b>
        <span className="faint">Question {current + 1} of {total}</span>
        <span className="spacer" />
        <span className="badge info">Integrity {Math.round(proctor.status.integrity)}</span>
        <span className={`timer${left != null && left < 300 ? " low" : ""}`}>⏱ {mm}</span>
        <button className="btn btn-primary" disabled={action.busy} onClick={() => confirm("Submit your assessment now?") && submit(false)}>
          {action.busy ? "Submitting..." : "Submit"}
        </button>
      </div>
      {proctor.warning && <div className="warn-toast">⚠️ {proctor.warning}</div>}
      {!fullscreen && (
        <div className="alert warn row-between" style={{ margin: "12px 20px 0" }}>
          <span>You exited full-screen mode. This is recorded.</span>
          <button className="btn btn-sm" onClick={enterFullscreen}>Return to full-screen</button>
        </div>
      )}
      <ErrorBox error={action.error} />
      <div className="exam-body">
        <main className="card">
          {current < a.mcqs.length ? (
            <MCQ q={a.mcqs[current]} index={current} value={answers[current]} onChange={(v) => setAnswers({ ...answers, [current]: v })} />
          ) : (
            <Coding
              p={a.coding[current - a.mcqs.length]}
              value={code[current - a.mcqs.length]}
              onChange={(v) => setCode({ ...code, [current - a.mcqs.length]: v })}
              result={results[current - a.mcqs.length]}
              onRun={() => run(current - a.mcqs.length)}
            />
          )}
          <div className="row" style={{ marginTop: 16 }}>
            <button className="btn" disabled={current === 0} onClick={() => setCurrent(current - 1)}>← Previous</button>
            <span className="spacer" />
            <button className="btn btn-primary" disabled={current === total - 1} onClick={() => setCurrent(current + 1)}>Next →</button>
          </div>
        </main>
        <aside className="stack">
          <CameraPanel proctor={proctor} />
          <div className="card" style={{ padding: 14 }}>
            <b className="small">Questions</b>
            <div className="q-nav" style={{ marginTop: 8 }}>
              {Array.from({ length: total }, (_, i) => (
                <button key={i} className={`${answered(i) ? "done" : ""} ${i === current ? "current" : ""}`} onClick={() => setCurrent(i)}>
                  {i < a.mcqs.length ? i + 1 : "</>"}
                </button>
              ))}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

function MCQ({ q, index, value, onChange }) {
  return (
    <div>
      <span className="chip">{q.skill}</span>
      <h2 style={{ margin: "10px 0 16px" }}>{index + 1}. {q.question}</h2>
      {q.options.map((o, j) => (
        <label key={j} className={`option${value === j ? " selected" : ""}`}>
          <input type="radio" name={`q${index}`} checked={value === j} onChange={() => onChange(j)} />
          <span>{o}</span>
        </label>
      ))}
    </div>
  );
}

function Coding({ p, value, onChange, result, onRun }) {
  return (
    <div className="stack">
      <div>
        <span className="chip">Python · {p.difficulty}</span>
        <h2 style={{ marginTop: 8 }}>{p.title}</h2>
        <p className="small" style={{ whiteSpace: "pre-wrap" }}>{p.description}</p>
        <p className="faint">Implement <code>{p.function_name}</code>. Your code runs in your browser (Pyodide). AI also reviews your code quality.</p>
      </div>
      <div className="editor-wrap">
        <Editor
          height="340px"
          defaultLanguage="python"
          value={value}
          onChange={(v) => onChange(v ?? "")}
          theme={matchMedia("(prefers-color-scheme: dark)").matches ? "vs-dark" : "light"}
          options={{ minimap: { enabled: false }, fontSize: 14, scrollBeyondLastLine: false, tabSize: 4 }}
        />
      </div>
      <div className="row">
        <button className="btn" disabled={result?.running} onClick={onRun}>{result?.running ? "Running..." : "▶ Run tests"}</button>
        {result && !result.running && (result.error
          ? <span className="badge bad">{result.error}</span>
          : <span className={`badge ${result.passed === result.total ? "ok" : "warn"}`}>{result.passed}/{result.total} tests passed</span>)}
      </div>
      {result?.results && (
        <div className="card" style={{ padding: 0 }}>
          {p.tests.map((t, i) => (
            <div key={i} className="test-row">
              <span>{result.results[i]?.pass ? "✅" : "❌"} {p.function_name}(*{t.args_json})</span>
              <span>expected {t.expected_json} · got {result.results[i]?.got}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
