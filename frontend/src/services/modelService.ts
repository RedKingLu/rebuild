/** Model Service — R5 ModelGateway API client.
 *
 * Calls /api/model/* endpoints for provider, profile, strategy,
 * self-test, call log, and platform assistant chat.
 */

import { get, post, del, put } from './client';

// ── Types ───────────────────────────────────────────────────────────

export interface ProviderInfo {
  provider_id: string;
  provider_name: string;
  provider_type: string;
  api_format: string;
  endpoint_openai: string;
  endpoint_anthropic: string;
  env_key_var: string;
  credential_status: string;
  key_source: string;
  status: string;
  model_count: number;
  origin: string;            // seed | user
  note: string;
  homepage: string;
  last_checked_at: string;
  capability_marker: string; // 14 种真实能力标记之一
  source_status: string;
  capability_status: string;
}

export interface ModelProfileInfo {
  profile_id: string;
  provider_id: string;
  model_name: string;
  display_name: string;
  capability_tags: string[];
  cost_tier: string;
  supports_streaming: boolean;
  supports_tool_calling: boolean;
  is_fusion_capable: boolean;
  context_window_note: string;
  recommended_use: string;
  not_recommended_use: string;
  status: string;
  source_status: string;
  capability_status: string;
}

export interface StrategyInfo {
  strategy_id: string;
  scope: string;
  default_profile_ref: string;
  fallback_profile_refs: string[];
  fallback_policy: string;
  retry_policy: Record<string, unknown>;
  fusion_allowed: boolean;
  streaming_allowed: boolean;
  tool_calling_allowed: boolean;
  trace_policy: string;
  audit_policy: string;
}

export interface ModelStatus {
  total_providers: number;
  configured_providers: number;
  reachable_providers: number;
  total_profiles: number;
  configured_profiles: number;
  default_profile: string;
  overall_status: string;
}

export interface SelfTestResult {
  provider_id: string;
  profile_id: string;
  model: string;
  status: string;
  latency_ms: number;
  credential_status: string;
  error_category: string;
  error_message: string;
  checked_at: string;
}

export interface CallLogEntry {
  model_call_id: string;
  provider_id: string;
  profile_id: string;
  strategy_id: string;
  selected_model: string;
  selection_reason: string;
  status: string;
  latency_ms: number;
  error_category: string;
  retry_count: number;
  fallback_used: boolean;
  usage_summary: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cache_hit_tokens?: number;
    cache_read_input_tokens?: number;
  };
  source: string;
  created_at: string;
  completed_at?: string;
}

export interface AssistantChatResult {
  reply: string;
  model: string;
  profile_id: string;
  provider_id: string;
  latency_ms: number;
  status: string;
  error_message: string;
  source: string;
}

// ── API functions ────────────────────────────────────────────────────

export function getModelStatus(): Promise<{ data: ModelStatus }> {
  return get<ModelStatus>('/model/status');
}

export function listProviders(): Promise<{ data: { providers: ProviderInfo[] } }> {
  return get<{ providers: ProviderInfo[] }>('/model/providers');
}

export function getProvider(providerId: string): Promise<{ data: ProviderInfo }> {
  return get<ProviderInfo>(`/model/providers/${providerId}`);
}

export function listProfiles(providerId?: string): Promise<{ data: { profiles: ModelProfileInfo[] } }> {
  const qs = providerId ? `?provider_id=${encodeURIComponent(providerId)}` : '';
  return get<{ profiles: ModelProfileInfo[] }>(`/model/profiles${qs}`);
}

export function listStrategies(): Promise<{ data: { strategies: StrategyInfo[] } }> {
  return get<{ strategies: StrategyInfo[] }>('/model/strategies');
}

export function selfTest(providerId: string, profileId?: string): Promise<{ data: SelfTestResult }> {
  return post<SelfTestResult>('/model/self-test', {
    provider_id: providerId,
    profile_id: profileId || null,
  });
}

export function listCalls(limit = 10, offset = 0): Promise<{ data: { calls: CallLogEntry[]; total: number; limit: number; offset: number } }> {
  return get<{ calls: CallLogEntry[]; total: number; limit: number; offset: number }>(`/model/calls?limit=${limit}&offset=${offset}`);
}

export interface CallLogPage {
  calls: CallLogEntry[];
  total: number;
  limit: number;
  offset: number;
}

export function assistantChat(message: string, profileId?: string): Promise<{ data: AssistantChatResult }> {
  return post<AssistantChatResult>('/assistant/chat', {
    message,
    profile_id: profileId || null,
  });
}

// ── 用户导入供应商 / 凭据 / 用量（R5-4 新增） ──────────────────────────

export interface ImportModelInput {
  model_name: string;
  display_name?: string;
  capability_tags?: string[];
  cost_tier?: string;
  supports_streaming?: boolean;
  supports_tool_calling?: boolean;
  context_window_note?: string;
  recommended_use?: string;
}

export interface CreateProviderInput {
  provider_id?: string;
  provider_name: string;
  provider_type?: string;
  api_format?: string;        // openai | anthropic
  endpoint_openai?: string;
  endpoint_anthropic?: string;
  env_key_var?: string;
  note?: string;
  homepage?: string;
  api_key?: string;           // 仅传给后端注入进程内存，永不落盘
  models?: ImportModelInput[];
}

export interface UsageInfo {
  total_calls: number;
  completed_calls: number;
  failed_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_hit_tokens: number;
  cache_read_tokens: number;
  cache_hit_rate: number;
  cost_available: boolean;
  cost_unavailable_reason: string;
  by_provider: { provider_id: string; calls: number; total_tokens: number }[];
  by_model: { model: string; calls: number; total_tokens: number }[];
  persisted: boolean;
}

export function createProvider(input: CreateProviderInput): Promise<{ data: ProviderInfo | null }> {
  return post<ProviderInfo | null>('/model/providers', input);
}

export function deleteProvider(providerId: string): Promise<{ data: { removed: boolean } }> {
  return del<{ removed: boolean }>(`/model/providers/${encodeURIComponent(providerId)}`);
}

export function setCredential(providerId: string, apiKey: string): Promise<{ data: ProviderInfo | null }> {
  return post<ProviderInfo | null>(`/model/providers/${encodeURIComponent(providerId)}/credential`, {
    api_key: apiKey,
  });
}

export function getUsage(): Promise<{ data: UsageInfo }> {
  return get<UsageInfo>('/model/usage');
}

export function updateStrategy(strategyId: string, body: { default_profile_ref?: string; fallback_profile_refs?: string[] }): Promise<{ data: StrategyInfo | null }> {
  return put<StrategyInfo | null>(`/model/strategies/${encodeURIComponent(strategyId)}`, body);
}

export function createStrategy(body: { strategy_id: string; default_profile_ref?: string; fallback_profile_refs?: string[] }): Promise<{ data: StrategyInfo | null; meta?: { not_connected_reason?: string } }> {
  return post<StrategyInfo | null>('/model/strategies', body);
}

export function deleteStrategy(strategyId: string): Promise<{ data: { removed: boolean } }> {
  return del<{ removed: boolean }>(`/model/strategies/${encodeURIComponent(strategyId)}`);
}

export interface UpdateProviderInput {
  provider_name?: string;
  api_format?: string;
  endpoint_openai?: string;
  endpoint_anthropic?: string;
  env_key_var?: string;
  note?: string;
  homepage?: string;
  models?: ImportModelInput[];
}

export function updateProvider(providerId: string, input: UpdateProviderInput): Promise<{ data: ProviderInfo | null }> {
  return put<ProviderInfo | null>(`/model/providers/${encodeURIComponent(providerId)}`, input);
}

// ── 真实能力标记（14 种，文档/06-UX与前端/06 §2/§18） ──────────────────
// 颜色+中文文字双通道。前端不臆测，状态来自后端。
export const CAPABILITY_MARKERS: Record<string, { label: string; color: string }> = {
  real_available:          { label: '真实可用',     color: 'var(--green)' },
  real_limited:            { label: '可用受限',     color: 'var(--blue)' },
  configured_not_verified: { label: '已配置未验证', color: 'var(--amber)' },
  credential_missing:      { label: '缺少凭据',     color: 'var(--orange)' },
  credential_invalid:      { label: '凭据无效',     color: 'var(--red)' },
  not_connected:           { label: '未连通',       color: 'var(--gray)' },
  mock:                    { label: '模拟数据',     color: 'var(--violet, #8b5cf6)' },
  static_demo:             { label: '静态演示',     color: 'var(--gray)' },
  read_only:              { label: '只读参考',     color: 'var(--blue)' },
  blocked_by_policy:       { label: '策略阻断',     color: 'var(--red)' },
  waiting_gate:            { label: '等待 Gate',    color: 'var(--amber)' },
  disabled:                { label: '已禁用',       color: 'var(--gray)' },
  deprecated:              { label: '已废弃',       color: 'var(--gray)' },
  unknown:                 { label: '状态未知',     color: 'var(--gray)' },
  not_checked:             { label: '未检测',       color: 'var(--gray)' },
};
