/** Gate and Authorization API service. */

import { get, post } from './client';
import type { Gate } from '../types';

interface GateListResponse {
  gates: Gate[];
  total: number;
}

interface GateDecisionResponse {
  gate: Gate;
  audit: Record<string, unknown>;
}

export async function fetchGates(projectId: string): Promise<Gate[]> {
  const resp = await get<GateListResponse>(`/projects/${projectId}/gates`);
  return resp.data.gates;
}

export async function fetchActiveGate(projectId: string): Promise<Gate | null> {
  const resp = await get<Gate | null>(`/projects/${projectId}/gates/active`);
  return resp.data;
}

export async function decideGate(
  projectId: string,
  gateId: string,
  decision: string,
  reason = '',
): Promise<GateDecisionResponse> {
  const resp = await post<GateDecisionResponse>(
    `/projects/${projectId}/gates/${gateId}/decision`,
    { decision, reason },
  );
  return resp.data;
}
