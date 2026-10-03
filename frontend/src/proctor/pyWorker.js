/* global importScripts, loadPyodide */
// Classic Web Worker: runs candidate Python with Pyodide (CPython compiled to WebAssembly).
// Running in a worker means an infinite loop can be killed without freezing the test page.
const PYODIDE = "https://cdn.jsdelivr.net/npm/pyodide@0.29.5/";
importScripts(PYODIDE + "pyodide.js");
const ready = loadPyodide({ indexURL: PYODIDE });

const HARNESS = `
import json
_p = json.loads(payload)
_out = []
try:
    _ns = {}
    exec(_p["code"], _ns)
    _fn = _ns.get(_p["fn"])
    if _fn is None:
        raise NameError("Function '" + _p["fn"] + "' is not defined")
    for _t in _p["tests"]:
        try:
            _args = json.loads(_t["args_json"])
            _res = _fn(*_args) if isinstance(_args, list) else _fn(_args)
            _got = json.dumps(_res, sort_keys=True)
            _exp = json.dumps(json.loads(_t["expected_json"]), sort_keys=True)
            _out.append({"pass": _got == _exp, "got": _got})
        except Exception as _e:
            _out.append({"pass": False, "got": type(_e).__name__ + ": " + str(_e)})
except Exception as _e:
    _out = {"error": type(_e).__name__ + ": " + str(_e)}
json.dumps(_out)
`;

self.onmessage = async (e) => {
  const { id, code, fn, tests } = e.data;
  try {
    const py = await ready;
    py.globals.set("payload", JSON.stringify({ code, fn, tests }));
    const out = JSON.parse(await py.runPythonAsync(HARNESS));
    self.postMessage({ id, ...(Array.isArray(out) ? { results: out } : { error: out.error }) });
  } catch (err) {
    self.postMessage({ id, error: String(err).slice(0, 500) });
  }
};
