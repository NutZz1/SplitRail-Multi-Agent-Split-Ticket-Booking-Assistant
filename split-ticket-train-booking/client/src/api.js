// Single place that knows where the API lives.
// Dev (Vite on :5173): VITE_API_BASE=http://localhost:8000 -> cross-origin call, CORS-allowed by FastAPI.
// Prod (built, served by FastAPI): VITE_API_BASE is unset -> relative /api/... on the same origin.
const BASE = import.meta.env.VITE_API_BASE || "";

async function request(path, options) {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  health: () => request("/api/health"),
  stations: (query) => request(`/api/stations?query=${encodeURIComponent(query)}`),
  demoTrains: () => request("/api/demo-trains"),
  demoScenarios: () => request("/api/demo-scenarios"),
  search: (body) =>
    request("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};
