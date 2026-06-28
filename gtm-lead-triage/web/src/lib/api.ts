const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_GTM_API_KEY || "";

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY) h["X-API-Key"] = API_KEY;
  return h;
}

function friendlyError(status: number, fallback: string): string {
  if (status === 401) return "Authentication failed — check your API key.";
  if (status === 429) return "Rate limit reached — please wait a moment and try again.";
  if (status === 503) return "Service is starting up — Render free tier cold-starts after idle. Try again in ~30 seconds.";
  return fallback;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(friendlyError(res.status, `${res.status} ${res.statusText}`));
  return res.json();
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(friendlyError(res.status, `${res.status} ${res.statusText}`));
  return res.json();
}
