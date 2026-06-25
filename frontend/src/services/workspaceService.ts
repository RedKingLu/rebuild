/** Workspace aggregate API service. */

import { get } from './client';
import type { Project, Run, Gate, Artifact, Evidence, Trace, Audit } from '../types';

export interface WorkspaceAggregate {
  project: Project | null;
  active_run: Run | null;
  stage_statuses: Record<string, string>;
  active_gate: Gate | null;
  pending_gates: Gate[];
  recent_artifacts: Artifact[];
  pending_evidence_gaps: Evidence[];
  recent_traces: Trace[];
  recent_audits: Audit[];
  file_index: unknown[];
  graph_status: {
    graph_capability_status: string;
    transition_mode: string;
    checkpoint_ref: string | null;
  };
  meta: Record<string, unknown>;
}

export async function fetchWorkspace(projectId: string): Promise<WorkspaceAggregate> {
  const resp = await get<WorkspaceAggregate>(`/projects/${projectId}/workspace`);
  return resp.data;
}
