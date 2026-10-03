/**
 * Real-time AI proctoring that runs entirely in the candidate's browser (free,
 * private, no GPU server needed):
 *  - MediaPipe FaceDetector: no face / multiple faces / looking away (head yaw)
 *  - MediaPipe ObjectDetector (EfficientDet, COCO): mobile phone, extra people
 *  - Web Audio: sustained talking in the room (assessment only)
 *  - Browser signals: tab switch, window blur, fullscreen exit, copy/paste, devtools
 * Events are batched to the backend every few seconds with small webcam snapshots.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { FaceDetector, FilesetResolver, ObjectDetector } from "@mediapipe/tasks-vision";

const FACE_MODEL =
  "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite";
const OBJECT_MODEL =
  "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float16/1/efficientdet_lite0.tflite";

const COOLDOWN_MS = {
  no_face: 15000, multiple_faces: 10000, looking_away: 20000, phone_detected: 15000, voice_detected: 30000,
  window_blur: 5000, tab_hidden: 3000, fullscreen_exit: 3000, copy: 5000, paste: 3000, large_paste: 3000,
  devtools: 10000, right_click: 10000, camera_off: 10000,
};

const CANDIDATE_MSG = {
  no_face: "We can't see your face. Please stay in front of the camera.",
  multiple_faces: "Another person was detected. The test must be taken alone.",
  looking_away: "Please keep your eyes on the screen.",
  phone_detected: "A phone was detected. Please put it away.",
  voice_detected: "Talking was detected. Please stay silent during the test.",
  tab_hidden: "You left the test tab. This has been recorded.",
  window_blur: "Please stay on the test window.",
  fullscreen_exit: "Please return to full-screen mode.",
  large_paste: "Pasting large amounts of text is recorded.",
  devtools: "Developer tools are not allowed.",
  camera_off: "Your camera turned off. Please re-enable it.",
};

async function createWithFallback(Klass, vision, options) {
  try {
    return await Klass.createFromOptions(vision, { ...options, baseOptions: { ...options.baseOptions, delegate: "GPU" } });
  } catch {
    return Klass.createFromOptions(vision, { ...options, baseOptions: { ...options.baseOptions, delegate: "CPU" } });
  }
}

export function useProctor({ token, phase, detectVoice = true, browserSignals = true }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const queue = useRef([]);
  const lastFired = useRef({});
  const streaks = useRef({ noFace: 0, away: 0, voice: 0 });
  const timers = useRef([]);
  const detectors = useRef({ face: null, object: null });
  const audio = useRef(null);
  const running = useRef(false);

  const [status, setStatus] = useState({ camera: "off", vision: "loading", faces: null, integrity: 100 });
  const [warning, setWarning] = useState(null);
  const [error, setError] = useState(null);

  const snapshot = useCallback(() => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return null;
    const c = document.createElement("canvas");
    c.width = 240;
    c.height = 180;
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.6);
  }, []);

  const flush = useCallback(
    async (keepalive = false) => {
      if (!queue.current.length) return;
      const events = queue.current.splice(0, 50);
      try {
        const res = await fetch(`/api/candidate/${token}/proctor`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ phase, events }),
          keepalive,
        });
        const data = await res.json();
        if (data?.integrity != null) setStatus((s) => ({ ...s, integrity: data.integrity }));
      } catch {
        queue.current.unshift(...events); // retry next flush
      }
    },
    [token, phase],
  );

  const report = useCallback(
    (type, severity, detail = "", { withSnapshot = false, notify = true } = {}) => {
      const now = Date.now();
      const cooldown = COOLDOWN_MS[type] ?? 0;
      if (cooldown && now - (lastFired.current[type] || 0) < cooldown) return;
      lastFired.current[type] = now;
      queue.current.push({ type, severity, detail, snapshot: withSnapshot ? snapshot() : null });
      if (notify && CANDIDATE_MSG[type]) {
        setWarning(CANDIDATE_MSG[type]);
        setTimeout(() => setWarning((w) => (w === CANDIDATE_MSG[type] ? null : w)), 4000);
      }
    },
    [snapshot],
  );

  // ------------------------------------------------------------ vision loop
  const analyseFrame = useCallback(() => {
    const v = videoRef.current;
    const { face } = detectors.current;
    if (!v || !face || v.readyState < 2) return;
    const result = face.detectForVideo(v, performance.now());
    const faces = result.detections.filter((d) => (d.categories?.[0]?.score ?? 1) > 0.6);
    setStatus((s) => (s.faces === faces.length ? s : { ...s, faces: faces.length }));

    if (faces.length === 0) {
      streaks.current.noFace += 1;
      if (streaks.current.noFace >= 3) report("no_face", "medium", "No face for 3+ seconds", { withSnapshot: true });
    } else streaks.current.noFace = 0;

    if (faces.length > 1) report("multiple_faces", "high", `${faces.length} faces in frame`, { withSnapshot: true });

    if (faces.length === 1) {
      const k = faces[0].keypoints || [];
      if (k.length >= 3) {
        const [rightEye, leftEye, nose] = k;
        const eyeDist = Math.abs(leftEye.x - rightEye.x) || 0.0001;
        const yaw = (nose.x - (leftEye.x + rightEye.x) / 2) / eyeDist; // ~0 when facing camera
        if (Math.abs(yaw) > 0.55) {
          streaks.current.away += 1;
          if (streaks.current.away >= 4) report("looking_away", "low", `Head turned (yaw ${yaw.toFixed(2)})`, { withSnapshot: true });
        } else streaks.current.away = 0;
      }
    }
  }, [report]);

  const analyseObjects = useCallback(() => {
    const v = videoRef.current;
    const { object } = detectors.current;
    if (!v || !object || v.readyState < 2) return;
    const result = object.detectForVideo(v, performance.now());
    const phones = result.detections.filter((d) => d.categories[0]?.categoryName === "cell phone");
    const people = result.detections.filter((d) => d.categories[0]?.categoryName === "person");
    if (phones.length) report("phone_detected", "high", `Phone (confidence ${(phones[0].categories[0].score * 100).toFixed(0)}%)`, { withSnapshot: true });
    if (people.length > 1) report("multiple_faces", "high", `${people.length} people detected`, { withSnapshot: true });
  }, [report]);

  const analyseAudio = useCallback(() => {
    const a = audio.current;
    if (!a) return;
    a.analyser.getFloatTimeDomainData(a.buf);
    let sum = 0;
    for (const x of a.buf) sum += x * x;
    const rms = Math.sqrt(sum / a.buf.length);
    if (rms > 0.04) {
      streaks.current.voice += 1;
      if (streaks.current.voice >= 3) report("voice_detected", "low", `Sustained sound (rms ${rms.toFixed(3)})`);
    } else streaks.current.voice = 0;
  }, [report]);

  // ------------------------------------------------------------ lifecycle
  const start = useCallback(async () => {
    if (running.current) return true;
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: true });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => {});
      }
      stream.getVideoTracks()[0]?.addEventListener("ended", () => report("camera_off", "high", "Camera track ended"));
      setStatus((s) => ({ ...s, camera: "on" }));
      if (detectVoice) {
        const ctx = new AudioContext();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 2048;
        ctx.createMediaStreamSource(stream).connect(analyser);
        audio.current = { ctx, analyser, buf: new Float32Array(analyser.fftSize) };
      }
    } catch (e) {
      setError("Camera and microphone access is required. Please allow it in your browser and reload.");
      return false;
    }
    running.current = true;
    queue.current.push({ type: "session_start", severity: "info", detail: navigator.userAgent.slice(0, 200), snapshot: snapshot() });

    // Load AI models (in parallel with the session; browser signals work immediately)
    (async () => {
      try {
        const vision = await FilesetResolver.forVisionTasks("/mediapipe");
        detectors.current.face = await createWithFallback(FaceDetector, vision, {
          baseOptions: { modelAssetPath: FACE_MODEL }, runningMode: "VIDEO", minDetectionConfidence: 0.6,
        });
        setStatus((s) => ({ ...s, vision: "on" }));
        detectors.current.object = await createWithFallback(ObjectDetector, vision, {
          baseOptions: { modelAssetPath: OBJECT_MODEL }, runningMode: "VIDEO", scoreThreshold: 0.5,
          categoryAllowlist: ["cell phone", "person"], maxResults: 5,
        });
      } catch (e) {
        console.warn("Vision models unavailable", e);
        setStatus((s) => ({ ...s, vision: detectors.current.face ? "on" : "unavailable" }));
      }
    })();

    timers.current.push(setInterval(() => { try { analyseFrame(); } catch { /* frame skipped */ } }, 1000));
    timers.current.push(setInterval(() => { try { analyseObjects(); } catch { /* frame skipped */ } }, 3000));
    if (detectVoice) timers.current.push(setInterval(analyseAudio, 1000));
    timers.current.push(setInterval(() => queue.current.push({ type: "snapshot", severity: "info", detail: "", snapshot: snapshot() }), 60000));
    timers.current.push(setInterval(() => flush(), 3000));
    return true;
  }, [analyseAudio, analyseFrame, analyseObjects, detectVoice, flush, report, snapshot]);

  const stop = useCallback(async () => {
    running.current = false;
    timers.current.forEach(clearInterval);
    timers.current = [];
    await flush(true);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    audio.current?.ctx.close().catch(() => {});
    audio.current = null;
    detectors.current.face?.close?.();
    detectors.current.object?.close?.();
    detectors.current = { face: null, object: null };
    setStatus((s) => ({ ...s, camera: "off" }));
  }, [flush]);

  // ------------------------------------------------------------ browser signals
  useEffect(() => {
    if (!browserSignals) return undefined;
    const onVisibility = () => running.current && document.hidden && report("tab_hidden", "medium", "Tab hidden or minimised", { withSnapshot: true });
    const onBlur = () => running.current && !document.hidden && report("window_blur", "low", "Window lost focus");
    const onFullscreen = () => running.current && !document.fullscreenElement && report("fullscreen_exit", "medium", "Exited full-screen");
    const onPaste = (e) => {
      if (!running.current) return;
      const n = e.clipboardData?.getData("text")?.length || 0;
      if (n > 80) report("large_paste", "medium", `Pasted ${n} characters`);
      else if (n > 0) report("paste", "low", `Pasted ${n} characters`, { notify: false });
    };
    const onCopy = () => running.current && report("copy", "low", "Copied text", { notify: false });
    const onContext = (e) => { if (running.current) { e.preventDefault(); report("right_click", "info", "Right-click blocked", { notify: false }); } };
    const onKey = (e) => {
      if (!running.current) return;
      const k = e.key?.toLowerCase();
      if (k === "f12" || ((e.ctrlKey || e.metaKey) && e.shiftKey && ["i", "j", "c"].includes(k))) {
        e.preventDefault();
        report("devtools", "medium", "DevTools shortcut");
      }
    };
    const onUnload = () => flush(true);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("blur", onBlur);
    document.addEventListener("fullscreenchange", onFullscreen);
    document.addEventListener("paste", onPaste, true);
    document.addEventListener("copy", onCopy, true);
    document.addEventListener("contextmenu", onContext);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("pagehide", onUnload);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("fullscreenchange", onFullscreen);
      document.removeEventListener("paste", onPaste, true);
      document.removeEventListener("copy", onCopy, true);
      document.removeEventListener("contextmenu", onContext);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("pagehide", onUnload);
    };
  }, [browserSignals, flush, report]);

  useEffect(() => () => { stop(); }, [stop]);

  // Re-attach the stream if the <video> element re-mounts.
  const attachVideo = useCallback((el) => {
    videoRef.current = el;
    if (el && streamRef.current && el.srcObject !== streamRef.current) {
      el.srcObject = streamRef.current;
      el.play().catch(() => {});
    }
  }, []);

  return { attachVideo, start, stop, status, warning, error, report };
}

export async function enterFullscreen() {
  try {
    if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
    return true;
  } catch {
    return false;
  }
}
