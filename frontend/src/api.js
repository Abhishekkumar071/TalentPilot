// Tiny fetch wrapper. In dev Vite proxies /api to FastAPI; in production the
// backend serves this app, so relative URLs work everywhere.
const TOKEN_KEY = "tp_hr_token";

export const auth = {
  get token() {
    try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
  },
  set(token) {
    try { localStorage.setItem(TOKEN_KEY, token); } catch { /* private mode */ }
  },
  clear() {
    try { localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ }
  },
};

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body, { form = false } = {}) {
  const headers = {};
  if (auth.token && path.startsWith("/api/hr")) headers.Authorization = `Bearer ${auth.token}`;
  let payload;
  if (body !== undefined) {
    if (form) payload = body;
    else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
  }
  const res = await fetch(path, { method, headers, body: payload });
  if (res.status === 401 && path.startsWith("/api/hr")) {
    auth.clear();
    if (!location.pathname.startsWith("/hr/login")) location.href = "/hr/login";
  }
  const isJson = res.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    let msg = data?.detail ?? data ?? res.statusText;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join(", ");
    throw new ApiError(typeof msg === "string" ? msg : JSON.stringify(msg), res.status);
  }
  return data;
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}) => request("POST", p, b),
  del: (p) => request("DELETE", p),
  upload: (p, formData) => request("POST", p, formData, { form: true }),
};

// Fetch a protected file (PDF) with the HR token and open it in a new tab.
export async function openProtectedFile(path) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${auth.token}` } });
  if (!res.ok) throw new ApiError("Could not load file", res.status);
  const url = URL.createObjectURL(await res.blob());
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
