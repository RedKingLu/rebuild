// Community service — wraps /api/community/* (R15-4-C4/C6).
import { get, post, unwrap } from "./client";

export interface CommunityResource {
  resource_id?: string;
  id?: string;
  name: string;
  display_name?: string;
  resource_type: string;
  description: string;
  version: string;
  tags?: string[];
  categories?: string[];
  license: string;
  source: string;
  verified: boolean;
  download_count: number;
  icon_url: string | null;
  checksum_sha256: string;
  namespace?: string;
  publisher?: string;
}
export interface CommunityResourceDetail extends CommunityResource {
  readme: string;
  files?: { name: string; size: number; download_url: string }[];
  dependencies?: unknown[];
  manifest_ref?: string;
}
export interface CommunityManifest {
  resource_id: string; version: string; type: string;
  files: { name: string; size: number; sha256: string }[];
  checksum_sha256: string; dependencies: unknown[];
}
export interface CommunityModel {
  model_id: string; display_name: string; provider_id: string; family: string;
  model_version: string; context_window: number | null; max_output_tokens: number | null;
  input_modalities: string[]; output_modalities: string[];
  capability_tags: string[]; task_tags: string[]; license: string | null;
  official_icon_url: string | null; official_url: string | null;
  availability_status: string;
}
export interface CommunityEval {
  eval_id: string; model_id: string; task_type: string | null; scenario: string | null;
  metric: string | null; score: number | null; sample_count: number | null;
  eval_method: string | null; source: string | null; limitations: string | null;
}

export interface SearchResult {
  totalSize: number; offset: number; resources: CommunityResource[];
}

async function search(params: Record<string, string>): Promise<SearchResult> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v && qs.set(k, v));
  const env = await get<SearchResult>(`/community/resources?${qs.toString()}`);
  return unwrap(env);
}

export const communityService = {
  search,
  async detail(id: string): Promise<CommunityResourceDetail> {
    const env = await get<CommunityResourceDetail>(`/community/resources/${id}`);
    return unwrap(env);
  },
  async manifest(id: string): Promise<CommunityManifest> {
    const env = await get<CommunityManifest>(`/community/resources/${id}/manifest`);
    return unwrap(env);
  },
  async importResource(id: string): Promise<{ resource_id: string; source: string; enabled: boolean }> {
    const env = await post<{ resource_id: string; source: string; enabled: boolean }>(
      `/community/resources/${id}/import`);
    return unwrap(env);
  },
  async models(params: Record<string, string> = {}): Promise<{ totalSize: number; models: CommunityModel[] }> {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v && qs.set(k, v));
    const env = await get<{ totalSize: number; models: CommunityModel[] }>(`/community/models?${qs}`);
    return unwrap(env);
  },
  async evaluations(): Promise<CommunityEval[]> {
    const env = await get<{ evaluations: CommunityEval[] }>(`/community/evaluations`);
    return unwrap(env).evaluations;
  },
  async status(): Promise<{ status: string; community_available?: boolean }> {
    const env = await get<{ status: string; community_available?: boolean; data?: unknown }>(`/community/status`);
    // status returns the connector status directly in data
    const d = unwrap(env);
    return d as { status: string; community_available?: boolean };
  },
};
