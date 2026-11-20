/** Fetch wrapper shared by every typed client module. Same-origin `/v1`; the
 * Gateway (oauth2-proxy) adds the identity header, so no credentials live here. */

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;
  readonly detail: string;

  constructor(path: string, status: number, detail: string) {
    super(`${path} → HTTP ${status}${detail ? `: ${detail}` : ""}`);
    this.name = "ApiError";
    this.path = path;
    this.status = status;
    this.detail = detail;
  }
}

export type Params = Record<string, string | number | boolean | null | undefined>;

/** Any plain object of query values; nullish and empty-string values are omitted. */
export function query(params: object = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

async function parseDetail(resp: Response): Promise<string> {
  try {
    const body = await resp.json();
    if (typeof body?.detail === "string") return body.detail;
    if (body?.detail) return JSON.stringify(body.detail);
  } catch {
    /* not JSON */
  }
  return "";
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, { ...init, headers: { Accept: "application/json", ...(init.headers ?? {}) } });
  if (!resp.ok) throw new ApiError(path, resp.status, await parseDetail(resp));
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

export const get = <T>(path: string, params?: object) => request<T>(`${path}${query(params)}`);

export const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const del = <T = void>(path: string) => request<T>(path, { method: "DELETE" });

export const isApiError = (e: unknown, status?: number): e is ApiError =>
  e instanceof ApiError && (status === undefined || e.status === status);
