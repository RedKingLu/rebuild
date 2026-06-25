/** Unified API client for rebuild backend.
 *
 * R4: Thin wrapper over fetch. All mock/in-memory API returns
 * use the unified envelope with meta.source_status.
 */

// R4 最小联调（C-B）：默认走同源相对路径 `/api`，由 Vite dev proxy 转发到后端，
// 免 CORS、不绑定端口。可用 VITE_API_BASE 覆盖（如指向远程后端）。
const BASE_URL = (import.meta.env?.VITE_API_BASE as string | undefined) || '/api';

export interface ApiEnvelope<T = unknown> {
  request_id: string;
  status: 'success' | 'error';
  data: T;
  meta: {
    source_status: string;
    capability_status: string;
    not_connected_reason?: string;
    generated_at: string;
    persistence?: string;
  };
  trace_ref: string | null;
  warnings: string[];
}

export interface ApiError {
  request_id?: string;
  status: string;
  error_code?: string;
  error_message?: string;
  detail?: string; // FastAPI default
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<ApiEnvelope<T>> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err: ApiError = body;
    throw new Error(err.detail || err.error_message || `HTTP ${res.status}`);
  }

  return res.json();
}

export function get<T>(path: string): Promise<ApiEnvelope<T>> {
  return request<T>(path);
}

export function post<T>(path: string, body?: unknown): Promise<ApiEnvelope<T>> {
  return request<T>(path, {
    method: 'POST',
    body: body ? JSON.stringify(body) : undefined,
  });
}

export function patch<T>(path: string, body?: unknown): Promise<ApiEnvelope<T>> {
  return request<T>(path, {
    method: 'PATCH',
    body: body ? JSON.stringify(body) : undefined,
  });
}

export function del<T>(path: string): Promise<ApiEnvelope<T>> {
  return request<T>(path, { method: 'DELETE' });
}

export function put<T>(path: string, body?: unknown): Promise<ApiEnvelope<T>> {
  return request<T>(path, {
    method: 'PUT',
    body: body ? JSON.stringify(body) : undefined,
  });
}
