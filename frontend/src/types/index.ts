/* Core type definitions for rebuild frontend
 * Aligned with current V26.1.1 terminology — no F0-F6, no Mission, no Phase
 */

// === Project ===
export type SourceType = 'local_dir' | 'git' | 'zip' | 'github';
export type ProjectStatus = 'created' | 'importing' | 'ready' | 'running' | 'archived' | 'error';
export type StageId = 'p0' | 'p1' | 'p2' | 'p3' | 'p4' | 'p5' | 'p6';
export type StageStatus = 'not_enabled' | 'pending' | 'in_progress' | 'waiting_gate' | 'blocked' | 'failed' | 'completed' | 'skipped';
export type RunStatus = 'created' | 'ready' | 'running' | 'paused' | 'waiting_gate' | 'blocked' | 'failed' | 'completed' | 'archived';
export type ExecutionMode = 'auto' | 'plan' | 'manual';
export type MockLevel = 'mock' | 'placeholder' | 'not_connected' | 'future';

export const STAGE_LABELS: Record<StageId, string> = {
  p0: 'P0 接入', p1: 'P1 建档', p2: 'P2 评估', p3: 'P3 规划',
  p4: 'P4 执行', p5: 'P5 验证', p6: 'P6 交付',
};

export interface Project {
  project_id: string;
  name: string;
  description: string;
  project_status: ProjectStatus;
  current_stage: StageId | null;
  current_run_id: string | null;
  active_gate: string | null;
  evidence_gap_count: number;
  source_type: SourceType;
  source_config?: Record<string, unknown>;
  workspace_status: 'importing' | 'ready' | 'unavailable' | 'blocked';
  created_at?: string;
  updated_at?: string;
  onboarding_done: boolean;
  coding_agent_ref?: string | null;  // D-078/R9-3A
  external_platform_scope?: 'none' | 'coding_only' | 'all_stages';  // D-088 / R11-2 B-6
  mock_level: MockLevel;
}

// === Run ===
export interface Run {
  run_id: string;
  project_id: string;
  run_goal: string;
  run_status: RunStatus;
  current_stage: StageId;
  started_at: string;
  updated_at: string;
  active_gate: string | null;
  can_pause: boolean;
  can_cancel: boolean;
  can_resume: boolean;
  stage_status: Record<StageId, StageStatus>;
  mock_level: MockLevel;
}

// === Gate ===
export type GateStatus = 'created' | 'waiting_decision' | 'approved' | 'rejected' | 'changes_requested' | 'blocked' | 'needs_more_info' | 'expired' | 'canceled' | 'resolved' | 'failed';
export type RiskLevel = 'L0' | 'L1' | 'L2' | 'L3' | 'L4' | 'L5';

export interface Gate {
  gate_id: string;
  gate_type: string;
  gate_status: GateStatus;
  project_id: string;
  run_id: string;
  stage: StageId;
  reason: string;
  risk_level: RiskLevel;
  summary: string;
  options: string[];
  recommended_option: string | null;
  mock_level: MockLevel;
}

// === Evidence ===
export type EvidenceStatus = 'candidate' | 'submitted' | 'under_validation' | 'validated' | 'insufficient' | 'rejected';
export type ValidationStatus = 'not_validated' | 'validation_pending' | 'validation_passed' | 'validation_failed' | 'validation_blocked';

export interface Evidence {
  evidence_id: string;
  evidence_type: string;
  evidence_status: EvidenceStatus;
  validation_status: ValidationStatus;
  stage: StageId;
  claim: string;
  summary: string;
  gap_description?: string;
  blocking: boolean;
  mock_level: MockLevel;
}

// Evidence Gap — pending gaps from uncertainty_manifest.json
// (backend EvidenceGapResponse: gap_id / evidence_type / description / blocking / stage)
export interface EvidenceGap {
  gap_id: string;
  evidence_type: string;
  description: string;
  blocking: boolean;
  stage: string;
  source_status?: string;
}

// === Artifact ===
export type ArtifactStatus = 'draft' | 'generated' | 'under_review' | 'accepted' | 'rejected' | 'evidence_candidate' | 'superseded' | 'archived';

export interface Artifact {
  artifact_id: string;
  artifact_type: string;
  title: string;
  stage: StageId;
  artifact_status: ArtifactStatus;
  is_evidence_candidate: boolean;
  content_hash: string;
  bytes: number;
  path: string;
  mock_level: MockLevel;
}

// === Trace / Audit ===
export interface Trace {
  trace_id: string;
  trace_type: string;
  run_id: string;
  stage: StageId;
  summary: string;
  created_at: string;
  mock_level: MockLevel;
}

export interface Audit {
  audit_id: string;
  audit_type: string;
  gate_id: string;
  risk_level: RiskLevel;
  decision: string;
  summary: string;
  created_at: string;
  mock_level: MockLevel;
}

// === File tree ===
export interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'dir';
  editable: boolean;
  children?: FileNode[];
}

export interface FileRoot {
  key: string;
  label: string;
  editable: boolean;
  children: FileNode[];
}
