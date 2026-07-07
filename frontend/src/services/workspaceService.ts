/** Workspace API service — file read/write, material tree, terminal execution. */

import { get, put, post, del } from './client';
import type { Project, Run, Gate, Artifact, EvidenceGap, Trace, Audit } from '../types';

export interface WorkspaceAggregate {
  project: Project | null;
  active_run: Run | null;
  stage_statuses: Record<string, string>;
  active_gate: Gate | null;
  pending_gates: Gate[];
  recent_artifacts: Artifact[];
  pending_evidence_gaps: EvidenceGap[];
  recent_traces: Trace[];
  recent_audits: Audit[];
  file_index: FileTreeNode[];
  material_index: FileTreeNode[];
  graph_status: {
    graph_capability_status: string;
    transition_mode: string;
    checkpoint_ref: string | null;
  };
  meta: Record<string, unknown>;
}

export interface FileTreeNode {
  key: string;
  label: string;
  editable: boolean;
  children: FileTreeEntry[];
}

export interface FileTreeEntry {
  name: string;
  path: string;
  type: 'file' | 'dir';
  editable: boolean;
  children?: FileTreeEntry[];
  bytes?: number;
}

export interface FileContent {
  path: string;
  content: string;
  bytes: number;
  editable: boolean;
  readonly: boolean;
  readonly_reason: string | null;
}

export interface FileTreeResponse {
  roots: FileTreeNode[];
}

export interface ExecuteRequest {
  command: string;
  language?: string;
  timeout?: number;
  session_id?: string | null;
}

export interface ExecuteResult {
  session_id?: string;
  exit_code: number;
  stdout: string;
  stderr: string;
  elapsed_ms: number;
  provider: string;
  execution_mode: string;
  blocked: boolean;
}

export async function fetchWorkspace(projectId: string): Promise<WorkspaceAggregate> {
  const resp = await get<WorkspaceAggregate>(`/projects/${projectId}/workspace`);
  return resp.data;
}

export async function fetchFileTree(projectId: string): Promise<FileTreeResponse> {
  const resp = await get<FileTreeResponse>(`/projects/${projectId}/files`);
  return resp.data;
}

export async function fetchMaterialTree(projectId: string): Promise<FileTreeResponse> {
  const resp = await get<FileTreeResponse>(`/projects/${projectId}/materials`);
  return resp.data;
}

export async function fetchFileContent(projectId: string, path: string): Promise<FileContent> {
  const resp = await get<FileContent>(`/projects/${projectId}/file?path=${encodeURIComponent(path)}`);
  return resp.data;
}

export async function saveFile(projectId: string, path: string, content: string): Promise<{ path: string; bytes: number; saved_at: string }> {
  const resp = await put<{ path: string; bytes: number; saved_at: string }>(`/projects/${projectId}/file`, { path, content });
  return resp.data;
}

export async function executeCommand(projectId: string, req: ExecuteRequest): Promise<ExecuteResult> {
  const resp = await post<ExecuteResult>(`/projects/${projectId}/execute`, {
    command: req.command,
    language: req.language || 'shell',
    timeout: req.timeout || 30,
    session_id: req.session_id || null,
  });
  return resp.data;
}

export async function fetchSessions(projectId: string): Promise<{ sessions: Array<Record<string, unknown>> }> {
  const resp = await get<{ sessions: Array<Record<string, unknown>> }>(`/projects/${projectId}/sessions`);
  return resp.data;
}

// Environment Profile (D-051, R8). Frontend consumer is the R9 onboarding wizard (§25 step 1).
export interface EnvironmentProfile {
  project_id: string;
  status: 'unknown' | 'declared' | 'ready';
  env_kind: 'local' | 'remote';
  language_hint: string | null;
  framework_hint: string | null;
  source: string;
  created_at: string;
  updated_at: string;
}

export async function fetchEnvironment(projectId: string): Promise<EnvironmentProfile> {
  const resp = await get<EnvironmentProfile>(`/projects/${projectId}/environment`);
  return resp.data;
}

export async function updateEnvironment(projectId: string, updates: Partial<EnvironmentProfile>): Promise<EnvironmentProfile> {
  const resp = await put<EnvironmentProfile>(`/projects/${projectId}/environment`, updates);
  return resp.data;
}

export async function fetchMode(projectId: string): Promise<string> {
  const resp = await get<{ execution_mode: string }>(`/projects/${projectId}/mode`);
  return resp.data.execution_mode;
}

/** R9-5-7 T6: persist execution mode (single source = workspace.json). Shared by
 *  the workspace top-bar ExecModeSwitch and the onboarding wizard step 4. */
export async function updateMode(projectId: string, mode: string): Promise<string> {
  const resp = await put<{ execution_mode: string }>(`/projects/${projectId}/mode`, { mode });
  return resp.data.execution_mode;
}

// ── R14-4: Environment binding / invocation / detection ────────────────

export interface EnvironmentBlock {
  default_binding_id: string | null;
  bindings: string[];
}

export interface RemoteInvocationItem {
  invocation_id: string;
  provider: string;
  trigger: string;
  command_digest: string;
  exit_code: number;
  risk_level: string;
  elapsed_ms: number;
  status: string;
  stdout_ref: string | null;
  stderr_ref: string | null;
  audit_ref: string | null;
  started_at: string | null;
}

export async function fetchEnvBlock(projectId: string): Promise<EnvironmentBlock> {
  const resp = await get<EnvironmentBlock>(`/projects/${projectId}/environment/block`);
  return resp.data;
}

export async function addBinding(projectId: string, remoteHostId: string, isDefault = false, remoteWorkdir?: string): Promise<EnvironmentBlock> {
  const resp = await post<EnvironmentBlock>(`/projects/${projectId}/environment/bindings`, {
    remote_host_id: remoteHostId, is_default: isDefault, remote_workdir: remoteWorkdir,
  });
  return resp.data;
}

export async function setDefaultBinding(projectId: string, bindingId: string | null): Promise<EnvironmentBlock> {
  const resp = await post<EnvironmentBlock>(`/projects/${projectId}/environment/default`, { binding_id: bindingId });
  return resp.data;
}

export async function removeBinding(projectId: string, bindingId: string): Promise<EnvironmentBlock> {
  const resp = await del<EnvironmentBlock>(`/projects/${projectId}/environment/bindings/${bindingId}`);
  return resp.data;
}

export async function fetchInvocations(projectId: string, limit = 20): Promise<RemoteInvocationItem[]> {
  const resp = await get<{ items: RemoteInvocationItem[] }>(`/projects/${projectId}/environment/invocations`, { limit });
  return resp.data.items;
}

export async function detectEnvironment(projectId: string, remoteHostId: string): Promise<Record<string, any>> {
  const resp = await get<Record<string, any>>(`/projects/${projectId}/environment/detect/${remoteHostId}`);
  return resp.data;
}
