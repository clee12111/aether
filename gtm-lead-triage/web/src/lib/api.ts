const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Log the API URL at load time so misconfig is visible in the browser console
if (typeof window !== "undefined") {
  console.log(`[GTM] API → ${API}`);
}

const defaultHeaders: Record<string, string> = { "Content-Type": "application/json" };

function friendlyError(status: number, fallback: string): string {
  if (status === 429) return "Rate limit reached — please wait a moment and try again.";
  if (status === 503) return "Service is starting up — try again in ~30 seconds.";
  return fallback;
}

async function fetchWithTimeout(input: RequestInfo, init: RequestInit, timeoutMs = 60_000): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("Request timed out — the backend may be waking up from a cold start. Please try again.");
    }
    throw new Error("Could not reach the API server. Check that NEXT_PUBLIC_API_URL is set correctly.");
  } finally {
    clearTimeout(timer);
  }
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetchWithTimeout(`${API}${path}`, {
    method: "POST",
    headers: defaultHeaders,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(friendlyError(res.status, `${res.status} ${res.statusText}`));
  return res.json();
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetchWithTimeout(`${API}${path}`, { headers: defaultHeaders, cache: "no-store" as RequestCache }, 15_000);
  if (!res.ok) throw new Error(friendlyError(res.status, `${res.status} ${res.statusText}`));
  return res.json();
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetchWithTimeout(`${API}${path}`, { method: "DELETE", headers: defaultHeaders });
  if (!res.ok) throw new Error(friendlyError(res.status, `${res.status} ${res.statusText}`));
  return res.json();
}

/** Fire-and-forget warmup ping — wakes Render from cold sleep. */
export function warmup(): void {
  fetch(`${API}/ready`).catch(() => {});
}
