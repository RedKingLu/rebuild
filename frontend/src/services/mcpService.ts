/** MCP Service — MCP Server management API client (Phase 12). */

import { get, post, put, del } from './client';

export interface MCPServer {
  mcp_id: string;
  name: string;
  description: string;
  transport: 'stdio' | 'sse';
  command: string | null;
  args: string[] | null;
  env_vars: Record<string, string> | null;
  sse_url: string | null;
  status: string;  // disconnected / connecting / connected / error
  last_checked_at: string | null;
  error_message: string | null;
  tools: MCPServerTool[] | null;
  origin: string;
  enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface MCPServerTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface MCPServerListData {
  servers: MCPServer[];
  total: number;
}

export async function listMCPServers(limit = 10, offset = 0): Promise<MCPServerListData> {
  const resp = await get<MCPServerListData>(`/mcp?limit=${limit}&offset=${offset}`);
  return resp as unknown as MCPServerListData;
}

export async function getMCPServer(id: string): Promise<MCPServer> {
  const resp = await get<MCPServer>(`/mcp/${id}`);
  return resp as unknown as MCPServer;
}

export async function createMCPServer(data: Partial<MCPServer>): Promise<MCPServer> {
  const resp = await post<MCPServer>('/mcp', data);
  return resp as unknown as MCPServer;
}

export async function updateMCPServer(id: string, data: Partial<MCPServer>): Promise<MCPServer> {
  const resp = await put<MCPServer>(`/mcp/${id}`, data);
  return resp as unknown as MCPServer;
}

export async function deleteMCPServer(id: string): Promise<void> {
  await del(`/mcp/${id}`);
}

export async function testMCPConnection(id: string): Promise<{ status: string; tools?: MCPServerTool[]; error?: string }> {
  const resp = await post<{ status: string; tools?: MCPServerTool[]; error?: string }>(`/mcp/${id}/test`);
  return resp as unknown as { status: string; tools?: MCPServerTool[]; error?: string };
}

// GitHub MCP preset
export const GITHUB_MCP_PRESET = {
  name: 'GitHub',
  description: 'GitHub API — 仓库管理、Issue、PR、代码搜索',
  transport: 'stdio' as const,
  command: 'npx',
  args: ['-y', '@modelcontextprotocol/server-github'],
  env_vars: { GITHUB_PERSONAL_ACCESS_TOKEN: '' },
};

/** 从官方社区 URL 导入 MCP 配置 */
export async function importMCPFromUrl(communityUrl: string): Promise<{ data: MCPServer }> {
  const resp = await fetch('/api/import/mcp', {
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
