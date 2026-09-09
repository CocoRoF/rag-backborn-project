"use client";
import { useAuth, type User } from "@/stores/auth";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public detail?: unknown) {
    super(message);
  }
}

const HAD_SESSION = "ragb:had-session";
export function markSession(on: boolean) {
  try { on ? localStorage.setItem(HAD_SESSION, "1") : localStorage.removeItem(HAD_SESSION); } catch { /* private mode */ }
}
export function hadSession(): boolean {
  try { return localStorage.getItem(HAD_SESSION) === "1"; } catch { return false; }
}

let refreshing: Promise<string | null> | null = null;

/** One in-flight refresh, shared. Parallel 401s must not each rotate the refresh token —
 *  single-use rotation means the second one would invalidate the first. */
export async function refreshAccess(): Promise<string | null> {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    try {
      const r = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
      if (!r.ok) { if (r.status === 401) markSession(false); return null; }
      const data = (await r.json()) as { access_token: string; user: User };
      useAuth.getState().setAuth(data.access_token, data.user);
      markSession(true);
      return data.access_token;
    } catch { return null; } finally { refreshing = null; }
  })();
  return refreshing;
}

export function redirectToLogin() {
  if (typeof window === "undefined") return;
  useAuth.getState().clear();
  markSession(false);
  if (window.location.pathname.startsWith("/login")) return;
  const here = window.location.pathname + window.location.search;
  window.location.assign(`/login?next=${encodeURIComponent(here)}`);
}

async function parseError(r: Response): Promise<ApiError> {
  let code = `http_${r.status}`, message = r.statusText, detail: unknown;
  try {
    const j = await r.json();
    if (j?.error) { code = j.error.code ?? code; message = j.error.message ?? message; detail = j.error.detail; }
    else if (j?.detail) { message = typeof j.detail === "string" ? j.detail : message; detail = j.detail; }
  } catch { /* not JSON */ }
  return new ApiError(r.status, code, message, detail);
}

interface Options extends Omit<RequestInit, "body"> { body?: unknown; auth?: boolean; raw?: boolean }

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const { body, auth = true, raw = false, headers: h, ...rest } = opts;
  const send = async (tok: string | null) => {
    const headers = new Headers(h ?? {});
    headers.set("Accept", "application/json");
    let payload: BodyInit | undefined;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) { headers.set("Content-Type", "application/json"); payload = JSON.stringify(body); }
    if (tok) headers.set("Authorization", `Bearer ${tok}`);
    return fetch(path, { ...rest, headers, body: payload, credentials: "same-origin" });
  };
  let tok = auth ? useAuth.getState().token : null;
  let r = await send(tok);
  if (r.status === 401 && auth) {
    const fresh = await refreshAccess();
    if (!fresh) { redirectToLogin(); throw await parseError(r); }
    tok = fresh;
    r = await send(tok);
    if (r.status === 401) { redirectToLogin(); throw await parseError(r); }
  }
  if (!r.ok) throw await parseError(r);
  if (raw) return r as unknown as T;
  if (r.status === 204) return undefined as T;
  const text = await r.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export const get = <T,>(p: string) => api<T>(p, { method: "GET" });
export const post = <T,>(p: string, body?: unknown) => api<T>(p, { method: "POST", body });
export const patch = <T,>(p: string, body?: unknown) => api<T>(p, { method: "PATCH", body });
export const put = <T,>(p: string, body?: unknown) => api<T>(p, { method: "PUT", body });
export const del = <T,>(p: string) => api<T>(p, { method: "DELETE" });

/** POST that returns a live SSE stream. `fetch` rather than EventSource: the request needs a
 *  bearer header and a JSON body, and EventSource can carry neither. */
export async function stream(
  path: string,
  body: unknown,
  onEvent: (e: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const run = async (tok: string | null): Promise<Response> =>
    fetch(path, {
      method: "POST", signal, credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
      body: JSON.stringify(body),
    });
  let r = await run(useAuth.getState().token);
  if (r.status === 401) {
    const fresh = await refreshAccess();
    if (!fresh) { redirectToLogin(); throw new ApiError(401, "unauthorized", "세션이 만료되었습니다"); }
    r = await run(fresh);
  }
  if (!r.ok || !r.body) throw await parseError(r);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line; a partial frame stays in the buffer.
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue;
        try { onEvent(JSON.parse(line.slice(5).trim())); } catch { /* keep-alive or partial */ }
      }
    }
  }
}
