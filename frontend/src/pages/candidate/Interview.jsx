import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api.js";
import { ErrorBox, Loading, Spinner, useAction, useData } from "../../components/ui.jsx";
import CameraPanel from "../../proctor/CameraPanel.jsx";
import { useProctor } from "../../proctor/useProctor.js";

const SR = typeof window !== "undefined" ? window.SpeechRecognition || window.webkitSpeechRecognition : null;

function speak(text, enabled) {
  return new Promise((resolve) => {
    if (!enabled || !window.speechSynthesis) return resolve();
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-IN";
    u.rate = 1;
    const voice = window.speechSynthesis.getVoices().find((v) => v.lang.startsWith("en") && /female|google/i.test(v.name));
    if (voice) u.voice = voice;
    // Chrome sometimes never fires onend; never let the interview get stuck.
    const fallback = setTimeout(resolve, 2000 + text.split(/\s+/).length * 450);
    const done = () => { clearTimeout(fallback); resolve(); };
    u.onend = done;
    u.onerror = done;
    window.speechSynthesis.speak(u);
  });
}

export default function Interview() {
  const { token } = useParams();
  const { data, error, loading } = useData(() => api.get(`/api/candidate/${token}/interview`), [token]);
  const proctor = useProctor({ token, phase: "interview", detectVoice: false });
  const [started, setStarted] = useState(false);
  const [finished, setFinished] = useState(false);

  if (loading) return <Loading text="Preparing your interview..." />;
  if (error) return <Centered><ErrorBox error={error} /></Centered>;
  if (finished || !data.available)
    return (
      <Centered>
        <div style={{ fontSize: 48 }}>🙌</div>
        <h1>{finished ? "Interview complete" : "Interview not available"}</h1>
        <p className="muted">
          {finished ? "Thank you! The hiring team will review your interview and get back to you by email." : "This interview link has been used or has expired."}
        </p>
      </Centered>
    );
  if (!started) return <Intro data={data} proctor={proctor} onStart={() => setStarted(true)} />;
  return <Room data={data} token={token} proctor={proctor} onFinish={() => setFinished(true)} />;
}

function Centered({ children }) {
  return (
    <div className="center" style={{ minHeight: "100vh", padding: 16, textAlign: "center" }}>
      <div className="card" style={{ maxWidth: 560 }}>{children}</div>
    </div>
  );
}

function Intro({ data, proctor, onStart }) {
  const [agree, setAgree] = useState(false);
  const action = useAction();
  return (
    <div className="public-main" style={{ maxWidth: 760 }}>
      <div className="card">
        <p className="faint">{data.company}</p>
        <h1>🎙️ AI interview: {data.job_title}</h1>
        <p className="muted">Hi {data.name}! An AI interviewer will ask you about {data.total_questions} questions about your experience,
          technical skills and how you approach problems. It adapts follow-up questions to your answers. Takes about 15-20 minutes.</p>
        <ul className="small">
          <li>Use Chrome or Edge with a working microphone and camera. Find a quiet place.</li>
          <li>The question is read aloud. Click the mic to answer by voice, or type your answer.</li>
          <li>Take a moment to think; there is no per-question timer.</li>
          <li>Camera-based proctoring is active, and a human reviews the recording summary.</li>
        </ul>
        {!SR && <div className="alert warn small">Voice input isn't supported in this browser. You can type your answers instead.</div>}
        <label className="checkbox field">
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
          <span className="small">I consent to camera and microphone use and AI evaluation of this interview.</span>
        </label>
        <ErrorBox error={action.error || proctor.error} />
        <button className="btn btn-primary btn-lg" disabled={!agree || action.busy}
          onClick={() => action.run(async () => { if (await proctor.start()) onStart(); })}>
          {action.busy ? "Starting camera..." : data.transcript.length ? "Resume interview" : "Start interview"}
        </button>
      </div>
    </div>
  );
}

function Room({ data, token, proctor, onFinish }) {
  const [transcript, setTranscript] = useState(data.transcript);
  const [answer, setAnswer] = useState("");
  const [listening, setListening] = useState(false);
  const [voiceOn, setVoiceOn] = useState(true);
  const [aiSpeaking, setAiSpeaking] = useState(false);
  const recRef = useRef(null);
  const finalRef = useRef("");
  const wantListen = useRef(false);
  const chatEnd = useRef(null);
  const action = useAction();
  const asked = transcript.filter((t) => t.role === "interviewer").length;

  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [transcript, aiSpeaking]);

  const stopListening = useCallback(() => {
    wantListen.current = false;
    recRef.current?.stop();
    setListening(false);
  }, []);

  const startListening = useCallback(() => {
    if (!SR) return;
    const rec = new SR();
    rec.lang = "en-IN";
    rec.continuous = true;
    rec.interimResults = true;
    finalRef.current = answer ? answer + " " : "";
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalRef.current += r[0].transcript + " ";
        else interim += r[0].transcript;
      }
      setAnswer((finalRef.current + interim).trimStart());
    };
    rec.onend = () => {
      if (wantListen.current) { try { rec.start(); } catch { /* already started */ } } // Chrome stops on silence
      else setListening(false);
    };
    rec.onerror = (e) => { if (e.error === "not-allowed") { wantListen.current = false; setListening(false); } };
    recRef.current = rec;
    wantListen.current = true;
    rec.start();
    setListening(true);
  }, [answer]);

  const send = useCallback((text) =>
    action.run(async () => {
      stopListening();
      const res = await api.post(`/api/candidate/${token}/interview/next`, { answer: text });
      setTranscript(res.transcript);
      setAnswer("");
      finalRef.current = "";
      setAiSpeaking(true);
      await speak(res.message, voiceOn);
      setAiSpeaking(false);
      if (res.done) {
        await proctor.stop();
        onFinish();
      } else if (voiceOn && SR) startListening();
    }), [action, onFinish, proctor, startListening, stopListening, token, voiceOn]);

  // First question (or re-speak last question when resuming)
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const last = transcript[transcript.length - 1];
    if (!transcript.length) send("");
    else if (last?.role === "interviewer") speak(last.text, voiceOn);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const awaitingAnswer = transcript[transcript.length - 1]?.role === "interviewer";

  return (
    <div className="exam-shell">
      <div className="exam-top">
        <b>🎙️ {data.job_title} · AI interview</b>
        <span className="faint">Question {Math.min(asked, data.total_questions)} of {data.total_questions}</span>
        <span className="spacer" />
        <label className="checkbox small" style={{ margin: 0 }}>
          <input type="checkbox" checked={voiceOn} onChange={(e) => setVoiceOn(e.target.checked)} /> Voice
        </label>
        <span className="badge info">Integrity {Math.round(proctor.status.integrity)}</span>
      </div>
      {proctor.warning && <div className="warn-toast">⚠️ {proctor.warning}</div>}
      <div className="exam-body">
        <main className="card stack">
          <div className="chat">
            {transcript.map((t, i) => (
              <div key={i} className={`bubble ${t.role === "interviewer" ? "ai" : "me"}`}>
                <div className="faint">{t.role === "interviewer" ? "🤖 Interviewer" : "🧑 You"}</div>
                {t.text}
              </div>
            ))}
            {(action.busy || aiSpeaking) && (
              <div className="bubble ai row"><Spinner /> {aiSpeaking ? "Speaking..." : "Thinking..."}</div>
            )}
            <div ref={chatEnd} />
          </div>
          <ErrorBox error={action.error} />
          {awaitingAnswer && !action.busy && (
            <div className="stack" style={{ gap: 10 }}>
              <textarea rows={4} value={answer} onChange={(e) => { setAnswer(e.target.value); finalRef.current = e.target.value + " "; }}
                placeholder={SR ? "Click the mic and speak, or type your answer here..." : "Type your answer here..."} />
              <div className="row">
                {SR && (
                  <button className={`mic-btn${listening ? " on" : ""}`} title={listening ? "Stop" : "Speak"} onClick={() => (listening ? stopListening() : startListening())}>
                    {listening ? "■" : "🎤"}
                  </button>
                )}
                <span className="muted small">{listening ? "Listening... click ■ when done, then submit." : ""}</span>
                <span className="spacer" />
                <button className="btn btn-primary btn-lg" disabled={!answer.trim()} onClick={() => send(answer.trim())}>Submit answer →</button>
              </div>
            </div>
          )}
        </main>
        <aside className="stack">
          <CameraPanel proctor={proctor} />
        </aside>
      </div>
    </div>
  );
}
