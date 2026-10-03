export default function CameraPanel({ proctor }) {
  const { status } = proctor;
  const faceTone = status.faces === 1 ? "ok" : status.faces == null ? "" : "bad";
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="cam">
        <video ref={proctor.attachVideo} muted playsInline autoPlay />
        <div className="cam-status">
          <span className="badge bad"><span className="dot pulse" /> REC</span>
        </div>
      </div>
      <div className="chips">
        <span className={`badge ${status.camera === "on" ? "ok" : "bad"}`}>📷 {status.camera}</span>
        <span className={`badge ${status.vision === "on" ? "ok" : status.vision === "loading" ? "info" : "warn"}`}>
          🧠 AI {status.vision}
        </span>
        <span className={`badge ${faceTone}`}>🙂 faces: {status.faces ?? "-"}</span>
      </div>
      <p className="faint" style={{ margin: 0 }}>
        AI proctoring is active. Unusual activity is recorded for a human reviewer.
      </p>
    </div>
  );
}
