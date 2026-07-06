/** Fusion Service — R13-7 /fusion 页面真实化。

 * 消费 /api/fusion/* 9 个真实端点（R13-4/5/6 施工），通过 unwrap() 解析统一 envelope。
 * 所有端点均为真实 API（非 store 静态数据、非 mock），遵循 D-097 去 mock。
 *
 * 与 ModelGateway 的对接通过上层已有的 /api/fusion/* 配置 API 完成；
 * 业务调用走 ModelGateway.call(user_override="fusion/...")（R13-6 分派），
 * 配置管理（CRUD / toggle / validate / 触发历史）走本 service。
 *
 * 设计遵循：中文优先 / 密钥脱敏（本 service 永不处理 Key）/ 线性图标约定（Skill §4.4）。
 */

import { get, post, put, del, unwrap } from './client';

// ── DTO ────────────────────────────────────────────────────────────────

export interface PanelParticipant {
  profile_ref: string;
  perspective?: string;
  weight?: number;
  temperature?: number;
  max_tokens?: number;
}

export interface JudgeConfig {
  profile_ref: string;
  dimensions?: string[];
  weights?: Record<string, number>;
  confidence_threshold?: number;
  temperature?: number;
}

export interface SynthesizerConfig {
  profile_ref: string;
  output_template?: string;
  writeback_target?: string;
  // 注意：synthesizer 不含工具配置（方案 E：Fusion 不执行工具，R13-2C accepted）
}

export interface FusionGlobalConfig {
  style?: string;             // 保守(budget) | 平衡 | 激进(frontier)
  enabled_stages?: string[];  // ["p2","p3","p5","p6"]
  trigger?: string;           // manual | auto
  cost_limit?: number;
  timeout_seconds?: number;
  audit_level?: string;       // summary | complete
  fallback_single_model?: string;
  excluded_providers?: string[];
  self_moa_enabled?: boolean;
}

export interface FusionProfileCreate {
  name: string;
  panel_participants?: PanelParticipant[];
  judge?: JudgeConfig;
  synthesizer?: SynthesizerConfig;
  global_config?: FusionGlobalConfig;
}

export interface FusionProfileUpdate {
  name?: string;
  panel_participants?: PanelParticipant[];
  judge?: JudgeConfig;
  synthesizer?: SynthesizerConfig;
  global_config?: FusionGlobalConfig;
}

export interface FusionProfile {
  fusion_profile_id: string;
  virtual_profile_ref: string;
  name: string;
  enabled: boolean;
  panel_participants: Record<string, unknown>[];
  judge: Record<string, unknown>;
  synthesizer: Record<string, unknown>;
  style: string;
  enabled_stages: string[];
  trigger: string;
  cost_limit: number;
  timeout_seconds: number;
  audit_level: string;
  fallback_single_model?: string;
  excluded_providers: string[];
  self_moa_enabled: boolean;
  max_fusion_depth: number;
  created_by: string;
  created_at: string;
  updated_at?: string | null;
  source_status: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface JudgeResult {
  scores: Record<string, number>;
  winner: string;
  confidence: string;          // high | medium | low
  consensus: string[];
  contradictions: string[];
  partial_coverage: string[];
  unique_insights: string[];
  blind_spots: string[];
}

export interface FusionRunParticipant {
  id: string;
  fusion_run_id: string;
  role: string;                // panel | judge | synthesizer | self_moa_sample
  profile_ref: string;
  provider_id: string;
  model: string;
  perspective: string;
  status: string;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost: number;
  trace_ref: string;
  model_call_id?: string;
  error_category: string;
}

export interface FusionRun {
  fusion_run_id: string;
  fusion_profile_id: string;
  project_id?: string;
  stage?: string;
  source: string;
  status: string;              // completed | degraded | failed
  strategy: string;            // panel_judge_synth | self_moa | fallback_single
  winner_ref?: string;
  confidence: string;
  degraded: boolean;
  degrade_reason?: string | null;
  cost_sum: number;
  latency_sum_ms: number;
  trace_refs: string[];
  audit_refs: string[];
  summary?: Record<string, unknown>;
  participants: FusionRunParticipant[];
  created_at: string;
  completed_at?: string | null;
  source_status: string;
}

export interface FusionMetadata {
  fusion_profile_id: string;
  fusion_run_id: string;
  strategy: string;
  participants: string[];
  panel_outputs: Record<string, unknown>[];
  judge_result: JudgeResult;
  synthesizer_result: { content: string; template: string };
  winner: string;
  confidence: string;
  cost_sum: number;
  latency_sum_ms: number;
  degraded: boolean;
  degrade_reason?: string | null;
  trace_refs: string[];
  audit_refs: string[];
  source_status: string;
}

// ── API functions ────────────────────────────────────────────────────────

/** GET /api/fusion/profiles — Profile cards 列表 */
export async function listFusionProfiles(): Promise<FusionProfile[]> {
  const r = await get<{ profiles: FusionProfile[]; total: number }>('/fusion/profiles');
  return unwrap(r).profiles || [];
}

/** POST /api/fusion/profiles — 创建（含 panel/judge/synthesizer 校验） */
export async function createFusionProfile(body: FusionProfileCreate): Promise<FusionProfile> {
  const r = await post<FusionProfile>('/fusion/profiles', body);
  return unwrap(r);
}

/** GET /api/fusion/profiles/{id} */
export async function getFusionProfile(id: string): Promise<FusionProfile> {
  const r = await get<FusionProfile>(`/fusion/profiles/${id}`);
  return unwrap(r);
}

/** PUT /api/fusion/profiles/{id}/config — 配置（Panel/Judge/Synthesizer/全局） */
export async function updateFusionProfile(id: string, body: FusionProfileUpdate): Promise<FusionProfile> {
  const r = await put<FusionProfile>(`/fusion/profiles/${id}/config`, body);
  return unwrap(r);
}

/** POST /api/fusion/profiles/{id}/toggle — 启用/禁用 */
export async function toggleFusionProfile(id: string): Promise<FusionProfile> {
  const r = await post<FusionProfile>(`/fusion/profiles/${id}/toggle`, {});
  return unwrap(r);
}

/** DELETE /api/fusion/profiles/{id} — 删除聚合模型（级联清理 runs/participants） */
export async function deleteFusionProfile(id: string): Promise<void> {
  const r = await del(`/fusion/profiles/${id}`);
  unwrap(r);
}

/** POST /api/fusion/profiles/{id}/validate — 校验（防递归 / 异构 / 预算 / 超时） */
export async function validateFusionProfile(id: string): Promise<ValidationResult> {
  const r = await post<ValidationResult>(`/fusion/profiles/${id}/validate`, {});
  return unwrap(r);
}

/** POST /api/fusion/profiles/{id}/trigger — 手动触发真实聚合 */
export interface TriggerRequest {
  project_id?: string;
  stage?: string;
  message?: string;
}
export interface FusionTriggerResult {
  trigger_status: string;
  fusion_run_id: string;
  strategy: string;
  degraded: boolean;
  degrade_reason?: string;
  content: string;
  fusion_metadata: FusionMetadata;
  [key: string]: unknown;
}
export async function triggerFusion(id: string, body: TriggerRequest): Promise<FusionTriggerResult> {
  const r = await post<Record<string, unknown>>(`/fusion/profiles/${id}/trigger`, body);
  return unwrap(r) as unknown as FusionTriggerResult;
}

/** GET /api/fusion/profiles/{id}/runs — 触发历史 */
export async function listFusionRuns(id: string): Promise<{ runs: FusionRun[]; total: number }> {
  const r = await get<{ runs: FusionRun[]; total: number }>(`/fusion/profiles/${id}/runs`);
  return unwrap(r);
}

/** GET /api/fusion/runs/{run_id} — 单次运行详情 */
export async function getFusionRun(runId: string): Promise<FusionRun> {
  const r = await get<FusionRun>(`/fusion/runs/${runId}`);
  return unwrap(r);
}
