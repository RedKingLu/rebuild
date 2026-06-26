/** Agent Service — R6/R12 Agent Definition API client (Phase 12 扩展). */

import { get, post, put, del } from './client';

export interface AgentDef {
  agent_id: string;
  agent_type: string;
  category: string;          // system | expert
  name: string;
  version: string;
  status: string;
  enabled: boolean;
  responsibilities: string;
  forbidden: string;
  context_recipe_ref: string | null;
  memory_policy_ref: string | null;
  model_policy_ref: string | null;
  gate_rules: string;
  failure_escalation: string;
  tool_policy_ref: string | null;
  skill_policy_ref: string | null;
  mcp_policy_ref: string | null;
  input_output_contract: string;
  artifact_evidence_contract: string;
  self_check_acceptance: string;
  context_recipe: Record<string, unknown> | null;
  memory_policy: Record<string, unknown> | null;
  custom_prompt: string | null;
  bound_skills: string[] | null;
  bound_tools: string[] | null;
  bound_mcps: string[] | null;
  credential_ref: string | null;
  origin: string;
  source_status: string;
  capability_status: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface AgentListData {
  agents: AgentDef[];
  total: number;
  source_status: string;
  capability_status: string;
}

export async function listAgents(agentType?: string, category?: string, limit = 100, offset = 0): Promise<AgentListData> {
  const params = new URLSearchParams();
  if (agentType) params.set('agent_type', agentType);
  if (category) params.set('category', category);
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  const qs = params.toString() ? `?${params.toString()}` : '';
  const resp = await get<AgentListData>(`/agents${qs}`);
  return resp as unknown as AgentListData;
}

export async function getAgent(agentId: string): Promise<AgentDef> {
  const resp = await get<AgentDef>(`/agents/${agentId}`);
  return resp as unknown as AgentDef;
}

export async function createAgent(data: Partial<AgentDef>): Promise<AgentDef> {
  const resp = await post<AgentDef>('/agents', data);
  return resp as unknown as AgentDef;
}

export async function updateAgent(agentId: string, data: Partial<AgentDef>): Promise<AgentDef> {
  const resp = await put<AgentDef>(`/agents/${agentId}`, data);
  return resp as unknown as AgentDef;
}

export async function deleteAgent(agentId: string): Promise<void> {
  await del(`/agents/${agentId}`);
}

// Import helper
export async function importAgentFile(file: File): Promise<AgentDef> {
  const formData = new FormData();
  formData.append('file', file);
  const resp = await fetch('/api/import/agent', { method: 'POST', body: formData });
  const json = await resp.json();
  return json.data as AgentDef;
}

/** 从官方社区 URL 导入 Agent */
export async function importAgentFromUrl(communityUrl: string): Promise<{ data: AgentDef }> {
  const resp = await fetch('/api/import/agent', {
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

/** 导出 Agent（zip 下载） */
export function exportAgent(agentId: string, name: string): void {
  const a = document.createElement('a');
  a.href = `/api/export/agent/${agentId}`;
  a.download = `${name}.zip`;
  a.click();
}

/** 切换 Agent 启用/禁用 */
export async function toggleAgentEnabled(agentId: string): Promise<{ data: { enabled: boolean } }> {
  const resp = await fetch(`/api/toggle/agent/${agentId}`, { method: 'PATCH' });
  if (!resp.ok) throw new Error('切换失败');
  return resp.json();
}
