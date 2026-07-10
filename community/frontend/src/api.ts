// Community frontend API client — talks to community-backend (8001).
// Dev: same-origin /api proxy (vite.config.ts → 8001). Prod: absolute base.

const BASE_URL =
  (import.meta.env && (import.meta.env as Record<string, string>).VITE_COMMUNITY_BASE) ||
  "/api";

async function getJSON<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(BASE_URL + path, window.location.origin);
  if (params) Object.entries(params).forEach(([k, v]) => v != null && url.searchParams.set(k, v));
  const r = await fetch(url.toString());
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return (await r.json()) as T;
}

export interface ResourceCard {
  id: string; resource_type: string; name: string; display_name: string;
  description: string; namespace: string; publisher: string; version: string;
  tags: string[]; categories: string[]; license: string; source: string;
  verified: boolean; deprecated: boolean; download_count: number;
  icon_url: string | null; checksum_sha256: string; updated_at: string | null;
}
export interface ResourceListResponse { totalSize: number; offset: number; resources: ResourceCard[]; }
export interface ResourceDetail extends ResourceCard { readme: string; versions: string[]; files: {name:string;size:number;download_url:string}[]; dependencies: unknown[]; manifest_ref: string; created_at: string | null; }
export interface ManifestResponse { resource_id: string; version: string; type: string; files: {name:string;size:number;sha256:string}[]; checksum_sha256: string; dependencies: unknown[]; }
export interface NewsItem { id: string; title: string; summary: string; body: string; published_at: string | null; url: string | null; pinned: boolean; }
export interface ModelEntry { model_id: string; display_name: string; provider_id: string; family: string; model_version: string; context_window: number | null; max_output_tokens: number | null; input_modalities: string[]; output_modalities: string[]; capability_tags: string[]; task_tags: string[]; license: string | null; official_icon_url: string | null; official_url: string | null; availability_status: string; source: string; updated_at: string | null; }
export interface ModelListResponse { totalSize: number; offset: number; models: ModelEntry[]; }
export interface EvaluationItem { eval_id: string; model_id: string; agent_type: string | null; task_type: string | null; scenario: string | null; metric: string | null; score: number | null; success_rate: number | null; cost_level: string | null; latency_level: string | null; sample_count: number | null; eval_method: string | null; eval_version: string | null; source: string | null; published_at: string | null; limitations: string | null; }
export interface StatusResponse { status: string; version: string; resource_count: number; model_count: number; evaluation_count: number; }

export const api = {
  status: () => getJSON<StatusResponse>("/status"),
  news: () => getJSON<{ news: NewsItem[] }>("/news"),
  resources: (p?: Record<string, string>) => getJSON<ResourceListResponse>("/resources", p),
  resource: (id: string) => getJSON<ResourceDetail>(`/resources/${id}`),
  manifest: (id: string) => getJSON<ManifestResponse>(`/resources/${id}/manifest`),
  models: (p?: Record<string, string>) => getJSON<ModelListResponse>("/models", p),
  evaluations: (p?: Record<string, string>) => getJSON<{ evaluations: EvaluationItem[] }>("/evaluations", p),
  downloadUrl: (id: string) => `${BASE_URL}/resources/${id}/download`,
};
