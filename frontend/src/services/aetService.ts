/** AET (Artifact, Evidence, Trace, Audit) API service. */

import { get } from './client';
import type { Artifact, Evidence, Trace, Audit } from '../types';

export async function fetchArtifacts(projectId: string): Promise<Artifact[]> {
  const resp = await get<{ artifacts: Artifact[] }>(`/projects/${projectId}/artifacts`);
  return resp.data.artifacts;
}

export async function fetchEvidence(projectId: string): Promise<Evidence[]> {
  const resp = await get<{ evidence: Evidence[] }>(`/projects/${projectId}/evidence`);
  return resp.data.evidence;
}

export async function fetchTraces(projectId: string): Promise<Trace[]> {
  const resp = await get<{ traces: Trace[] }>(`/projects/${projectId}/trace`);
  return resp.data.traces;
}

export async function fetchAudits(projectId: string): Promise<Audit[]> {
  const resp = await get<{ audits: Audit[] }>(`/projects/${projectId}/audit`);
  return resp.data.audits;
}
