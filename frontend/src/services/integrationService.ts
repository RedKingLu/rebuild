/** Integration Service — Git OAuth / Remote / OpenCode / Feishu APIs. */

import { get, post, del } from './client';

// ── Types ──

export interface GitAccountInfo {
  account_id: string; platform: string; username: string;
  avatar_url?: string; status: string; created_at: string;
}

export interface GitRepoInfo {
  repo_id: number; name: string; full_name: string;
  description: string; private: boolean;
  clone_url: string; ssh_url: string;
  default_branch: string; language?: string;
  updated_at?: string; stargazers_count: number;
}

export interface GitHostInfo {
  git_host_id: string; name: string; repo_url: string; platform: string;
  auth_type: string; credential_ref?: string; default_branch: string;
  status: string; last_connected_at?: string; created_at: string; updated_at: string;
  enabled: boolean;
}

export interface RemoteHostInfo {
  remote_host_id: string; name: string; host_type: string; address: string;
  port: number; os_name?: string; credential_ref?: string;
  status: string; last_connected_at?: string; created_at: string; updated_at: string;
  enabled: boolean;
}

export interface IntegrationSummary {
  git: { connected: number; total: number };
  remote: { connected: number; total: number };
  execution: { connected: number; total: number };
  other: { connected: number; total: number };
}

// ── Git OAuth APIs ──

export async function getGithubAuthorizeUrl(): Promise<string> {
  const resp = await get<any>('/integrations/git/oauth/github/authorize');
  return resp.data?.authorize_url || '';
}

export async function listGitAccounts(): Promise<GitAccountInfo[]> {
  const resp = await get<any>('/integrations/git/accounts');
  return resp.data || [];
}

export async function listAccountRepos(accountId: string): Promise<GitRepoInfo[]> {
  const resp = await get<any>(`/integrations/git/accounts/${accountId}/repos`);
  return resp.data || [];
}

export async function disconnectGitAccount(accountId: string): Promise<void> {
  await del(`/integrations/git/accounts/${accountId}`);
}

// ── Git Host APIs (legacy PAT-based) ──

export async function listGitHosts(): Promise<GitHostInfo[]> {
  const resp = await get<any>('/integrations/git');
  return resp.data || [];
}

export async function createGitHost(data: any): Promise<GitHostInfo> {
  const resp = await post<any>('/integrations/git', data);
  return resp.data;
}

export async function deleteGitHost(id: string): Promise<void> {
  await del(`/integrations/git/${id}`);
}

export async function cloneGitRepo(id: string): Promise<any> {
  const resp = await post<any>(`/integrations/git/${id}/clone`, {});
  return resp.data;
}

// ── Remote APIs ──

export async function listRemoteHosts(): Promise<RemoteHostInfo[]> {
  const resp = await get<any>('/integrations/remote');
  return resp.data || [];
}

export async function createRemoteHost(data: any): Promise<RemoteHostInfo> {
  const resp = await post<any>('/integrations/remote', data);
  return resp.data;
}

export async function deleteRemoteHost(id: string): Promise<void> {
  await del(`/integrations/remote/${id}`);
}

export async function testRemoteHost(id: string): Promise<any> {
  const resp = await post<any>(`/integrations/remote/${id}/test`, {});
  return resp.data;
}

// ── OpenCode APIs ──

export async function executeOpenCode(code: string, language?: string, timeout?: number, model?: string): Promise<any> {
  const resp = await post<any>('/integrations/execution/opencode', { code, language, timeout, model });
  return resp.data;
}

export async function getOpenCodeStatus(): Promise<any> {
  const resp = await get<any>('/integrations/execution/opencode/status');
  return resp.data;
}

// ── Feishu APIs ──

export async function getFeishuConfig(): Promise<any> {
  const resp = await get<any>('/integrations/feishu');
  return resp.data;
}

export async function updateFeishuConfig(data: any): Promise<any> {
  const resp = await post<any>('/integrations/feishu', data);
  return resp.data;
}

export async function testFeishu(): Promise<any> {
  const resp = await post<any>('/integrations/feishu/test', {});
  return resp.data;
}

// ── Summary ──

export async function getIntegrationSummary(): Promise<IntegrationSummary> {
  const resp = await get<any>('/integrations/summary');
  return resp.data;
}

// ── Status labels ──

export const INTEGRATION_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  connected: { label: '已连接', color: 'var(--green)' },
  disconnected: { label: '已断开', color: 'var(--ink-3)' },
  not_configured: { label: '未配置', color: 'var(--ink-2)' },
  unconfigured: { label: '未配置', color: 'var(--ink-2)' },
  error: { label: '异常', color: 'var(--red)' },
  cloning: { label: '克隆中', color: 'var(--amber)' },
  testing: { label: '测试中', color: 'var(--amber)' },
  configured_not_verified: { label: '已配置未验证', color: 'var(--amber)' },
  credential_missing: { label: '缺凭证', color: 'var(--red)' },
};
