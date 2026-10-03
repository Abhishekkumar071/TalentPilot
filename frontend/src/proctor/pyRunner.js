// Runs tests in a Pyodide worker with a time limit; restarts the worker on timeout.
let worker = null;
let seq = 0;
const pending = new Map();

function getWorker() {
  if (!worker) {
    worker = new Worker(new URL("./pyWorker.js", import.meta.url));
    worker.onmessage = (e) => {
      const p = pending.get(e.data.id);
      if (p) {
        pending.delete(e.data.id);
        clearTimeout(p.timer);
        p.resolve(e.data);
      }
    };
  }
  return worker;
}

export function warmUpPython() {
  getWorker();
}

export function runTests({ code, fn, tests, timeoutMs = 10000 }) {
  return new Promise((resolve) => {
    const id = ++seq;
    const timer = setTimeout(() => {
      pending.delete(id);
      worker?.terminate();
      worker = null; // next run gets a fresh interpreter
      resolve({ id, error: "Time limit exceeded (possible infinite loop)" });
    }, timeoutMs);
    pending.set(id, { resolve, timer });
    getWorker().postMessage({ id, code, fn, tests });
  });
}
