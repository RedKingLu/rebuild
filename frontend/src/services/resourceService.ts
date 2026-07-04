/** Resource Service — R6/R12 Resource Registry API client (Phase 12 扩展: 全 CRUD). */

import { get, post, put, del, unwrap } from './client';

export interface ResourceEntry {
  resource_id: string;
  resource_type: string;
  name: string;
  description: string;
  version: string;
  source_type: string;
  source_trust_level: string;
  source_path_or_ref: string | null;
  risk_level: string;
  status: string;
  permission_scope: string;
  capabilities: Record<string, unknown> | null;
  allowed_actions: string[] | null;
  blocked_actions: string[] | null;
  type_metadata: Record<string, unknown> | null;
  source_status: string;
  capability_status: string;
  enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ResourceListData {
  resources: ResourceEntry[];
  total: number;
  source_status: string;
  capability_status: string;
}

export interface RegistrySummary {
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  by_risk_level: Record<string, number>;
  total: number;
  source_status: string;
  capability_status: string;
}

export async function listResources(params?: {
  type?: string;
  source?: string;
  trust?: string;
  risk?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<ResourceListData> {
  const q = new URLSearchParams();
  if (params?.type) q.set('type', params.type);
  if (params?.source) q.set('source', params.source);
  if (params?.trust) q.set('trust', params.trust);
  if (params?.risk) q.set('risk', params.risk);
  if (params?.status) q.set('status', params.status);
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.offset != null) q.set('offset', String(params.offset));
  const qs = q.toString() ? `?${q.toString()}` : '';
  const resp = await get<ResourceListData>(`/resources${qs}`);
  return unwrap<ResourceListData>(resp);
}

export async function getResource(resourceId: string): Promise<ResourceEntry> {
  const resp = await get<ResourceEntry>(`/resources/${resourceId}`);
  return unwrap<ResourceEntry>(resp);
}

export async function getRegistrySummary(): Promise<RegistrySummary> {
  const resp = await get<RegistrySummary>('/resources/registry');
  return unwrap<RegistrySummary>(resp);
}

export async function createResource(data: Partial<ResourceEntry>): Promise<ResourceEntry> {
  const resp = await post<ResourceEntry>('/resources', data);
  return unwrap<ResourceEntry>(resp);
}

export async function updateResource(resourceId: string, data: Partial<ResourceEntry>): Promise<ResourceEntry> {
  const resp = await put<ResourceEntry>(`/resources/${resourceId}`, data);
  return unwrap<ResourceEntry>(resp);
}

export async function deleteResource(resourceId: string): Promise<void> {
  await del(`/resources/${resourceId}`);
}

/** 创建其他资源：zip 上传 */
export async function uploadResourceZip(
  name: string,
  resourceType: string,
  description: string,
  riskLevel: string,
  file: File,
): Promise<{ data: ResourceEntry }> {
  const fd = new FormData();
  fd.append('name', name);
  fd.append('resource_type', resourceType);
  fd.append('description', description);
  fd.append('risk_level', riskLevel);
  fd.append('file', file);
  const resp = await fetch('/api/upload/resource', { method: 'POST', body: fd });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || '上传失败');
  }
  return resp.json();
}

/** 导出 Resource（zip 下载） */
export function exportResource(resourceId: string, name: string): void {
  const a = document.createElement('a');
  a.href = `/api/export/resource/${resourceId}`;
  a.download = `${name}.zip`;
  a.click();
}

/** 切换 Resource 启用/禁用 */
export async function toggleResource(resourceId: string): Promise<{ data: { enabled: boolean } }> {
  const resp = await fetch(`/api/toggle/resource/${resourceId}`, { method: 'PATCH' });
  if (!resp.ok) throw new Error('切换失败');
  return resp.json();
}

/** 从官方社区 URL 导入资源 */
export async function importResourceFromUrl(communityUrl: string): Promise<{ data: ResourceEntry }> {
  const resp = await fetch('/api/import/resource', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ community_url: communityUrl }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || '导入失败');
  }
  return resp.json();
}

export async function importResourceFile(file: File, resourceType?: string): Promise<ResourceEntry> {
  const formData = new FormData();
  formData.append('file', file);
  if (resourceType) formData.append('resource_type', resourceType);
  const resp = await fetch('/api/import/resource', { method: 'POST', body: formData });
  const json = await resp.json();
  return json.data as ResourceEntry;
}

// Resource type labels (中文)
export const RESOURCE_TYPE_LABELS: Record<string, string> = {
  agent: 'Agent',
  skill: 'Skill',
  tool: '工具',
  hook: 'Hook（钩子）',
  case: '案例',
  knowledge: '知识',
  template: '模板',
  mcp: 'MCP',
  expert_agent: '专家 Agent',
  policy: '策略',
  deterministic_transformer: '确定性转换器',
  execution_provider: '执行提供者',
};

export const RISK_LABELS: Record<string, string> = {
  L0: 'L0 · 无风险',
  L1: 'L1 · 只读',
  L2: 'L2 · 低风险',
  L3: 'L3 · 中等',
  L4: 'L4 · 高风险',
  L5: 'L5 · 最高',
};
