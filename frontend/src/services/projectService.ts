/** Project API service. */

import { get, post, patch } from './client';
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

export async function createProject(name: string, description = '', sourceType = 'local_dir'): Promise<Project> {
  const resp = await post<Project>('/projects', {
    name,
    description,
    source_type: sourceType,
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
