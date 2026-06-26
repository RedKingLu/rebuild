/** Project API service. */

import { get, post, patch, del } from './client';
import type { Project } from '../types';

interface ProjectListResponse {
  projects: Project[];
  total: number;
}

export async function fetchProjects(): Promise<Project[]> {
  const resp = await get<ProjectListResponse>('/projects');
  return resp.data.projects;
}

/** R4 最小联调（C-B）：返回项目列表 + 后端 envelope 的真实 source_status，
 *  供页面显示数据来源（mock / in_memory / …）。 */
export async function fetchProjectsWithSource(): Promise<{ projects: Project[]; sourceStatus: string }> {
  const resp = await get<ProjectListResponse>('/projects');
  return { projects: resp.data.projects, sourceStatus: resp.meta?.source_status ?? 'unknown' };
}

export async function fetchProject(id: string): Promise<Project> {
  const resp = await get<Project>(`/projects/${id}`);
  return resp.data;
}

export async function createProject(params: {
  name: string;
  description?: string;
  source_type?: string;
  source_config?: Record<string, unknown>;
}): Promise<Project> {
  const resp = await post<Project>('/projects', {
    name: params.name,
    description: params.description ?? '',
    source_type: params.source_type ?? 'local_dir',
    source_config: params.source_config ?? {},
  });
  return resp.data;
}

export async function updateProject(id: string, fields: Record<string, unknown>): Promise<Project> {
  const resp = await patch<Project>(`/projects/${id}`, fields);
  return resp.data;
}

export async function archiveProject(id: string): Promise<void> {
  await post(`/projects/${id}/archive`);
}

export async function deleteProject(id: string): Promise<void> {
  await del(`/projects/${id}`);
}
