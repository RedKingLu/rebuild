/** Run API service. */

import { get, post } from './client';
import type { Run } from '../types';

interface RunListResponse {
  runs: Run[];
  total: number;
}

export async function fetchRuns(projectId: string): Promise<Run[]> {
  const resp = await get<RunListResponse>(`/projects/${projectId}/runs`);
  return resp.data.runs;
}

export async function fetchRun(projectId: string, runId: string): Promise<Run> {
  const resp = await get<Run>(`/projects/${projectId}/runs/${runId}`);
  return resp.data;
}

export async function createRun(projectId: string, runGoal: string, mode = 'plan'): Promise<Run> {
  const resp = await post<Run>(`/projects/${projectId}/runs`, {
    run_goal: runGoal,
    mode,
  });
  return resp.data;
}

export async function startRun(projectId: string, runId: string): Promise<Run> {
  const resp = await post<Run>(`/projects/${projectId}/runs/${runId}/start`);
  return resp.data;
}

export async function pauseRun(projectId: string, runId: string): Promise<Run> {
  const resp = await post<Run>(`/projects/${projectId}/runs/${runId}/pause`);
  return resp.data;
}

export async function cancelRun(projectId: string, runId: string): Promise<Run> {
  const resp = await post<Run>(`/projects/${projectId}/runs/${runId}/cancel`);
  return resp.data;
}

export async function resumeRun(projectId: string, runId: string, decision: string): Promise<Run> {
  const resp = await post<Run>(`/projects/${projectId}/runs/${runId}/resume`, { decision });
  return resp.data;
}
