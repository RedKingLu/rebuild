/** Skill Service — R7 更新：zip 上传 / 导出 / toggle / 社区 URL 导入 */

import { get, post, put, del } from './client';

export interface SkillDef {
  skill_id: string;
  name: string;
  version: string;
  series: 'R' | 'P';
  category: string;
  description: string;
  required_model_policy: string | null;
  required_tools: string[] | null;
  required_context: string[] | null;
  status: string;
  skill_source: string | null;
  directory_path: string | null;
  enabled: boolean;
  source_status: string;
  capability_status: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface SkillListData {
  skills: SkillDef[];
  total: number;
  source_status: string;
  capability_status: string;
}

export async function listSkills(series?: string, category?: string, limit = 10, offset = 0): Promise<SkillListData> {
  const params = new URLSearchParams();
  if (series) params.set('series', series);
  if (category) params.set('category', category);
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  const qs = params.toString() ? `?${params.toString()}` : '';
  const resp = await get<SkillListData>(`/skills${qs}`);
  return resp as unknown as SkillListData;
}

export async function getSkill(skillId: string): Promise<SkillDef> {
  const resp = await get<SkillDef>(`/skills/${skillId}`);
  return resp as unknown as SkillDef;
}

export async function createSkill(data: Partial<SkillDef>): Promise<SkillDef> {
  const resp = await post<SkillDef>('/skills', data);
  return resp as unknown as SkillDef;
}

export async function updateSkill(skillId: string, data: Partial<SkillDef>): Promise<SkillDef> {
  const resp = await put<SkillDef>(`/skills/${skillId}`, data);
  return resp as unknown as SkillDef;
}

export async function deleteSkill(skillId: string): Promise<void> {
  await del(`/skills/${skillId}`);
}

/** 创建 Skill：zip 上传 */
export async function uploadSkillZip(
  name: string,
  category: string,
  description: string,
  file: File,
  series = 'P',
): Promise<{ data: SkillDef }> {
  const fd = new FormData();
  fd.append('name', name);
  fd.append('category', category);
  fd.append('description', description);
  fd.append('series', series);
  fd.append('file', file);
  const resp = await fetch('/api/upload/skill', { method: 'POST', body: fd });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || '上传失败');
  }
  return resp.json();
}

/** 导出 Skill（zip 下载） */
export function exportSkill(skillId: string, name: string): void {
  const a = document.createElement('a');
  a.href = `/api/export/skill/${skillId}`;
  a.download = `${name}.zip`;
  a.click();
}

/** 切换启用/禁用 */
export async function toggleSkill(skillId: string): Promise<{ data: { enabled: boolean } }> {
  const resp = await fetch(`/api/toggle/skill/${skillId}`, { method: 'PATCH' });
  if (!resp.ok) throw new Error('切换失败');
  return resp.json();
}

/** 从官方社区 URL 导入 Skill */
export async function importSkillFromUrl(communityUrl: string): Promise<{ data: SkillDef }> {
  const resp = await fetch('/api/import/skill', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ community_url: communityUrl }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || '导入失败');
  }
  return resp.json();
}
